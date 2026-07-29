import subprocess, os, shutil, asyncio, logging, json as _json, re, tempfile, ntpath, shlex
from urllib.parse import quote

_log = logging.getLogger("dashi_tools")


def _dashi_skill_root() -> str:
    """Prefer the deployed /app/skills copy, then fall back to repo app/skills."""
    app_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(app_dir), "skills", "dashi-ppt"),
        os.path.join(app_dir, "skills", "dashi-ppt"),
    ]
    return next(
        (candidate for candidate in candidates if os.path.isdir(os.path.join(candidate, "project"))),
        candidates[-1],
    )


def _dashi_paths() -> tuple[str, str]:
    root = _dashi_skill_root()
    return root, os.path.join(root, "project")


def _resolve_project_output_path(
    project_root: str,
    path: str,
    *,
    suffix: str,
) -> tuple[str, str]:
    """Resolve a caller path strictly inside ``<project>/output``.

    Agent-provided tool arguments must remain relative so they cannot address
    arbitrary container files. ``realpath`` also catches an existing symlink
    inside output that points elsewhere, while ``commonpath`` avoids prefix
    collisions such as ``output-evil``.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty relative string")
    if "\x00" in path:
        raise ValueError("path contains a NUL byte")

    portable = path.strip().replace("\\", "/")
    drive, _ = ntpath.splitdrive(portable)
    if drive or os.path.isabs(portable) or ntpath.isabs(portable):
        raise ValueError("absolute paths are not allowed")
    if any(part == ".." for part in portable.split("/")):
        raise ValueError("parent traversal is not allowed")

    project_abs = os.path.realpath(os.path.abspath(project_root))
    output_abs = os.path.realpath(os.path.join(project_abs, "output"))
    target_abs = os.path.realpath(os.path.join(project_abs, os.path.normpath(portable)))
    try:
        common = os.path.commonpath([output_abs, target_abs])
    except ValueError as exc:
        raise ValueError("path is on a different filesystem root") from exc
    if common != output_abs:
        raise ValueError("path must stay inside the project output directory")

    if suffix and not target_abs.lower().endswith(suffix.lower()):
        raise ValueError(f"path must end with {suffix}")

    relative = os.path.relpath(target_abs, project_abs).replace("\\", "/")
    return relative, target_abs


def _project_subprocess_env(project_root: str) -> dict:
    """Pin scripts that honor INIT_CWD to the already-confined project root."""
    env = os.environ.copy()
    env["INIT_CWD"] = os.path.realpath(os.path.abspath(project_root))
    return env


def _resolve_media_source(project_root: str, path: str) -> str:
    """Resolve media from uploads or an existing project output directory."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError("media path must be a non-empty string")

    app_dir = os.path.dirname(os.path.abspath(__file__))
    app_root = os.path.dirname(app_dir)
    allowed_roots = [
        os.path.realpath(os.path.join(app_root, "uploads")),
        os.path.realpath(os.path.join(project_root, "output")),
    ]

    if os.path.isabs(path) or ntpath.isabs(path):
        candidates = [os.path.realpath(path)]
    else:
        portable = path.replace("\\", "/")
        candidates = [
            os.path.realpath(os.path.join(app_root, portable)),
            os.path.realpath(os.path.join(project_root, portable)),
        ]

    for candidate in candidates:
        for allowed_root in allowed_roots:
            try:
                if os.path.commonpath([allowed_root, candidate]) != allowed_root:
                    continue
            except ValueError:
                continue
            if os.path.isfile(candidate):
                return candidate
    raise ValueError("media path must be an existing file inside uploads/ or project/output/")


def _project_relative_path(project_root: str, path: str) -> str | None:
    """Return a URL-safe project-relative path, or None for paths outside the project."""
    project_abs = os.path.abspath(project_root)
    target_abs = os.path.abspath(path if os.path.isabs(path) else os.path.join(project_abs, path))
    try:
        if os.path.commonpath([project_abs, target_abs]) != project_abs:
            return None
    except ValueError:
        return None
    return os.path.relpath(target_abs, project_abs).replace("\\", "/")


def _artifact_url(route: str, relative_path: str) -> str:
    return f"/{route}?path={quote(relative_path, safe='/')}"


def _copy_pptx_to_uploads(pptx_path: str, deck_name: str, user_id: str = "default_user") -> str | None:
    """Copy exported PPTX to user uploads directory for workspace access.
    Returns the relative path within uploads, or None on failure."""
    try:
        uploads_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
        user_dir = os.path.join(uploads_dir, user_id)
        os.makedirs(user_dir, exist_ok=True)
        dest = os.path.join(user_dir, f"{deck_name}.pptx")
        shutil.copy2(pptx_path, dest)
        _log.info("Copied PPTX to uploads: %s", dest)
        return f"{deck_name}.pptx"
    except Exception as exc:
        _log.warning("Failed to copy PPTX to uploads: %s", exc)
        return None


def _atomic_write_json(path: str, value) -> None:
    """Publish JSON atomically after a complete UTF-8 write and fsync."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    candidate_path = None
    try:
        fd, candidate_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            dir=directory,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            _json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(candidate_path, path)
        candidate_path = None

        # Persist the renamed directory entry where directory fsync is supported.
        try:
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if candidate_path and os.path.exists(candidate_path):
            try:
                os.unlink(candidate_path)
            except OSError:
                _log.warning("failed to remove JSON write candidate: %s", candidate_path)


def _inspect_layout_items(raw_stdout: str) -> list:
    """Return the layout records from every supported inspect-layout envelope."""
    data = _json.loads(raw_stdout)
    if isinstance(data, dict):
        items = data.get("layouts")
        if items is None:
            items = data.get("results")
        if items is None:
            items = [data]
    else:
        items = data
    return items if isinstance(items, list) else []


def _empty_value_for_shape(shape):
    """Build an empty JSON value from an inspect-layout shape without coercion."""
    if isinstance(shape, dict):
        return {key: _empty_value_for_shape(value) for key, value in shape.items()}
    if isinstance(shape, list):
        return [_empty_value_for_shape(value) for value in shape]
    if shape == "number":
        return 0
    if shape == "boolean":
        return False
    return ""


_MISSING = object()
_PROP_PATH_TOKEN = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)(\[\])?$")


def _prop_path_tokens(path: str) -> tuple[tuple[str, bool], ...]:
    """Parse fillPlan paths, including array wildcards like ``stats[].bars``."""
    if not isinstance(path, str):
        return ()
    tokens = []
    for part in path.split("."):
        matched = _PROP_PATH_TOKEN.fullmatch(part)
        if not matched:
            return ()
        tokens.append((matched.group(1), bool(matched.group(2))))
    return tuple(tokens)


def _prop_path_parts(path: str) -> tuple[str, ...]:
    """Parse a fillPlan object-only path such as ``copy.metrics``."""
    tokens = _prop_path_tokens(path)
    if not tokens or any(is_array for _, is_array in tokens):
        return ()
    return tuple(name for name, _ in tokens)


def _get_prop_path(props: dict, path: str, default=_MISSING):
    parts = _prop_path_parts(path)
    if not parts:
        return default
    current = props
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _set_prop_path(props: dict, path: str, value) -> bool:
    parts = _prop_path_parts(path)
    if not parts or not isinstance(props, dict):
        return False
    current = props
    for part in parts[:-1]:
        child = current.get(part, _MISSING)
        if child is _MISSING:
            child = {}
            current[part] = child
        if not isinstance(child, dict):
            return False
        current = child
    current[parts[-1]] = value
    return True


def _prop_leaf_targets(props: dict, path: str, *, create: bool = False) -> list[tuple[dict, str]]:
    """Return parent/key pairs addressed by an object/wildcard fillPlan path."""
    tokens = _prop_path_tokens(path)
    if not tokens or not isinstance(props, dict):
        return []

    current_nodes = [props]
    for name, is_array in tokens[:-1]:
        next_nodes = []
        for current in current_nodes:
            if not isinstance(current, dict):
                continue
            child = current.get(name, _MISSING)
            if is_array:
                if isinstance(child, list):
                    next_nodes.extend(item for item in child if isinstance(item, dict))
                continue
            if child is _MISSING and create:
                child = {}
                current[name] = child
            if isinstance(child, dict):
                next_nodes.append(child)
        current_nodes = next_nodes
        if not current_nodes:
            return []

    leaf_name, _leaf_is_array = tokens[-1]
    return [(current, leaf_name) for current in current_nodes if isinstance(current, dict)]


def _is_json_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# Essential fields to keep from inspect-layout output (avoid context pollution)
def _filter_layout_query(raw_stdout: str) -> str:
    """Compact layout-query output while retaining selection-relevant metadata."""
    try:
        data = _json.loads(raw_stdout)
        layouts = data.get("layouts", [])
        compact = {
            "theme": data.get("theme", ""),
            "themeDisplayName": data.get("themeDisplayName"),
            "role": data.get("role", ""),
            "keyword": data.get("keyword"),
            "seed": data.get("seed"),
            "count": len(layouts),
            "layouts": [],
        }
        for l in layouts:
            compact["layouts"].append({
                "layout": l.get("layout", "?"),
                "label": l.get("label", ""),
                "roles": l.get("roles", []),
                "copyKeys": l.get("copyKeys", []),
                "arrayKeys": l.get("arrayKeys", []),
                "mediaSlots": [
                    {
                        "fieldPath": slot.get("fieldPath"),
                        "countKey": slot.get("countKey"),
                        "maxCount": slot.get("maxCount", slot.get("max")),
                        "acceptedKinds": slot.get("acceptedKinds", []),
                    }
                    for slot in (l.get("mediaSlots") or [])
                ],
            })
        return _json.dumps(compact, ensure_ascii=False)
    except:
        return raw_stdout
def _filter_inspect_output(raw_stdout: str) -> str:
    """Compact inspect output while preserving the canonical JS fillPlan types."""
    try:
        items = _inspect_layout_items(raw_stdout)
        if not items:
            return raw_stdout
        compact = []
        for item in items:
            fill_plan = item.get("fillPlan") or {"text": [], "arrays": [], "media": []}
            props = {}
            for text_plan in fill_plan.get("text") or []:
                key = text_plan.get("key")
                if not key:
                    continue
                type_name = text_plan.get("type", "string")
                max_chars = text_plan.get("maxChars")
                props[key] = f"{type_name}:{max_chars}" if max_chars else type_name

            arrays = {}
            for array_plan in fill_plan.get("arrays") or []:
                key = array_plan.get("key")
                if not key:
                    continue
                arrays[key] = {
                    "cnt": array_plan.get("visibleCount"),
                    "vis": array_plan.get("visibleCount"),
                    "max": array_plan.get("maxCount"),
                    **{
                        name: value
                        for name, value in array_plan.items()
                        if name not in {"key", "visibleCount", "maxCount"}
                    },
                }

            entry = {
                "layout": item.get("layout", "?"),
                "label": item.get("label", ""),
                "props": props,
                "allowedKeys": item.get("allowedPublicPropKeys") or item.get("allowedPropKeys", []),
                "fillPlan": fill_plan,
            }
            if arrays:
                entry["arrays"] = arrays
            compact.append(entry)
        return _json.dumps(compact, ensure_ascii=False, indent=2)
    except:
        pass
    return raw_stdout


_DENSE_TEXT_FIELD_TOKENS = (
    "lead",
    "sub",
    "intro",
    "conclusion",
    "quote",
    "tagline",
    "caption",
    "message",
    "summary",
    "note",
)
_DISPLAY_TEXT_FIELD_TOKENS = ("display", "headline", "hot")


def _with_recommended_text_budgets(fill_plan: dict) -> dict:
    """Add conservative authoring budgets without changing the real contract.

    ``maxChars`` remains the template's hard validation limit.  The additional
    ``recommendedMaxChars`` gives the Agent headroom for CJK wrapping and makes
    a first-pass write less likely to fail by one or two characters.
    """
    result = _json.loads(_json.dumps(fill_plan, ensure_ascii=False))
    for text_plan in result.get("text") or []:
        max_chars = text_plan.get("maxChars")
        if (
            not isinstance(max_chars, int)
            or isinstance(max_chars, bool)
            or max_chars <= 12
        ):
            continue
        key = str(text_plan.get("key") or "")
        leaf = key.rsplit(".", 1)[-1].lower()
        if any(token in leaf for token in _DENSE_TEXT_FIELD_TOKENS):
            ratio = 0.55
        elif any(token in leaf for token in _DISPLAY_TEXT_FIELD_TOKENS):
            ratio = 0.65
        else:
            continue
        recommended = max(8, int(max_chars * ratio))
        if recommended < max_chars:
            text_plan["recommendedMaxChars"] = recommended
    return result


def _build_prop_skeleton(matched: dict) -> dict:
    """Build a default-filled props skeleton for a layout from inspect-layout output.

    Returns a dict with correct types per the layout's propShapes:
      - number fields -> 0
      - number[] arrays -> []  (count will be set in dashi_write_goal)
      - string[] arrays -> []
      - object (single nested object) -> {<sub_keys>: ""}
      - object (list of nested objects, e.g. metrics[]) -> []
      - tuple array (itemShape list) -> []  (dashi_write_goal fills per shape)
      - string fields -> ""

    The skeleton guarantees JSON parses + types match, so dashi_write_goal can later
    partial-merge by key without LLM having to redefine structure.
    """
    skeleton = {}
    shapes = matched.get("propShapes", {}) or {}
    keys = matched.get("copyKeys", []) or []
    field_contracts = matched.get("fieldContracts", []) or []

    # Build a lookup of fieldContracts by key for arrays/objects with rich metadata
    fc_by_key = {fc.get("key"): fc for fc in field_contracts if fc.get("key")}

    seen_roots = set()
    for copy_key in keys:
        # copyKeys contains paths such as specs[][] and steps[].name. goal.json
        # must only contain the public root prop, never those contract paths.
        k = str(copy_key).split(".", 1)[0].replace("[]", "")
        if not k or k in seen_roots:
            continue
        seen_roots.add(k)
        shape = shapes.get(k, "?")
        fc = fc_by_key.get(k, {})

        # Detect tuple/nested arrays: itemShape is a list with multiple primitive types
        item_shape = fc.get("itemShape")
        nested_arrays = fc.get("nestedArrays")

        if isinstance(shape, dict):
            # Preserve nested primitive types reported by inspect-layout.
            skeleton[k] = _empty_value_for_shape(shape)
        elif isinstance(shape, list):
            # Array field: empty list initially, dashi_write_goal will fill to count
            skeleton[k] = []
        elif shape == "number":
            skeleton[k] = 0
        else:
            # Default string
            skeleton[k] = ""

    # Also pre-fill countKey numeric fields (e.g., metricCount -> 0)
    # These usually aren't in copyKeys but are referenced by arrays
    seen = set(skeleton.keys())
    for fc in field_contracts:
        ck = fc.get("countKey")
        if ck and ck not in seen and ck in shapes:
            sk = shapes[ck]
            if sk == "number":
                dc = fc.get("defaultCount") or fc.get("defaultVisibleCount") or 0
                skeleton[ck] = dc
            else:
                skeleton[ck] = 0
            seen.add(ck)

    # fillPlan is the canonical JS contract. Include array/media roots and their
    # numeric count controls even when copyKeys/fieldContracts omit them.
    fill_plan = matched.get("fillPlan") or {}
    for plan in [*(fill_plan.get("arrays") or []), *(fill_plan.get("media") or [])]:
        key = plan.get("key")
        key_tokens = _prop_path_tokens(key)
        if key and not key_tokens:
            _log.warning("ignored unsupported fillPlan prop path: %s", key)
        elif key and not any(is_array for _, is_array in key_tokens) and _get_prop_path(skeleton, key) is _MISSING:
            if not _set_prop_path(skeleton, key, []):
                _log.warning("ignored unsupported fillPlan prop path: %s", key)
        count_key = plan.get("countKey")
        count_tokens = _prop_path_tokens(count_key)
        if count_key and not count_tokens:
            _log.warning("ignored unsupported fillPlan count path: %s", count_key)
        elif (
            count_key
            and not any(is_array for _, is_array in count_tokens)
            and _get_prop_path(skeleton, count_key) is _MISSING
        ):
            visible_count = plan.get("visibleCount")
            count_value = visible_count if _is_json_number(visible_count) else 0
            if not _set_prop_path(skeleton, count_key, count_value):
                _log.warning("ignored unsupported fillPlan count path: %s", count_key)

    return skeleton


def _merge_prop_skeleton(template: dict, override: dict) -> dict:
    """Merge user-provided props onto a typed template.

    Rules:
      - Keep template keys (correct types) when override doesn't provide them.
      - For matching keys, override wins.
      - If override provides wrong type (e.g., string for number), coerce when safe.
      - For object fields: deep-merge rather than replace.
    """
    import copy as _copy

    if not isinstance(template, dict):
        template = {}
    if not isinstance(override, dict):
        return _copy.deepcopy(template)

    result = _copy.deepcopy(template)

    for k, v in override.items():
        if k not in result:
            # New key not in template: accept as-is (forward-compat)
            result[k] = v
            continue

        cur = result[k]
        if isinstance(cur, dict) and isinstance(v, dict):
            # Deep-merge object fields
            for sk, sv in v.items():
                if isinstance(cur.get(sk), dict) and isinstance(sv, dict):
                    cur[sk] = _merge_prop_skeleton(cur[sk], sv)
                else:
                    cur[sk] = sv
        elif isinstance(cur, list) and not isinstance(v, list):
            # Type mismatch: keep template's empty list
            continue
        elif isinstance(cur, (int, float)) and not isinstance(v, (int, float, bool)):
            # Type mismatch for number: try coercion
            if isinstance(v, str):
                try:
                    result[k] = int(v) if "." not in v else float(v)
                except (ValueError, TypeError):
                    continue
            else:
                continue
        elif isinstance(cur, str) and not isinstance(v, str):
            # Number -> string for string field
            result[k] = str(v)
        else:
            result[k] = v

    return result


def _sanitize_json_string(s: str) -> str:
    """Try to clean common LLM-emitted JSON errors before raising parse error.

    Fixes:
      - Leading/trailing whitespace + BOM
      - Trailing commas before } or ]
      - Smart/curly quotes from Chinese IME (\u201c\u201d\u2018\u2019 -> ASCII)
      - Chinese fullwidth punctuation inside JSON structure (：→:, ，→,, etc.)
      - Unescaped newlines inside strings
      - Missing commas between adjacent string/number literals
      - Single quotes used as string delimiters

    Returns cleaned string. Raises json.JSONDecodeError if still invalid.
    """
    if not isinstance(s, str):
        raise _json.JSONDecodeError("not a string", str(s)[:50], 0)

    import re as _re

    # Strip BOM and whitespace
    s = s.strip().lstrip("\ufeff")

    # Replace smart/curly quotes with ASCII double quotes
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2018", "'").replace("\u2019", "'")

    # Replace Chinese fullwidth colons and commas that appear OUTSIDE string
    # values (heuristic: adjacent to quotes, braces, brackets)
    # We do a conservative pass: replace fullwidth chars next to JSON structure
    s = _re.sub(r'\uff1a(\s*")', r':\1', s)  # ：" -> :"
    s = _re.sub(r'(")\uff1a', r'\1:', s)      # "： -> ":
    s = _re.sub(r'\uff0c(\s*")', r',\1', s)   # ，" -> ,"
    s = _re.sub(r'(")\uff0c', r'\1,', s)      # "， -> ",
    s = _re.sub(r'\uff0c(\s*\d)', r',\1', s)  # ，<digit> -> ,<digit>
    s = _re.sub(r'(\d)\uff0c(\s*")', r'\1,\2', s)  # <digit>，" -> <digit>,"
    s = _re.sub(r'\uff0c(\s*[\]\}])', r',\1', s)   # ，] or ，}
    s = _re.sub(r'([\]\}])\uff0c', r'\1,', s)       # ]，or }，

    # Remove trailing commas: ,] or ,}
    s = _re.sub(r",(\s*[\]\}])", r"\1", s)

    # Fix missing commas between adjacent "value" "value" or } { or ] [ patterns
    s = _re.sub(r'("\s*)(\n\s*")', r'\1,\2', s)  # "\n" -> ",\n"
    s = _re.sub(r'(\}\s*)(\n\s*\{)', r'\1,\2', s)  # }\n{ -> },\n{
    s = _re.sub(r'(\]\s*)(\n\s*\[)', r'\1,\2', s)  # ]\n[ -> ],\n[
    s = _re.sub(r'("\s+)(")', r'\1,\2', s)  # " " on same line -> ", "

    # Fix unescaped literal newlines inside strings (replace with \n)
    # This is a best-effort heuristic: find strings spanning multiple lines
    def _fix_newlines_in_strings(text):
        result = []
        in_string = False
        escape_next = False
        for ch in text:
            if escape_next:
                result.append(ch)
                escape_next = False
                continue
            if ch == '\\' and in_string:
                result.append(ch)
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                result.append(ch)
                continue
            if in_string and ch == '\n':
                result.append('\\n')
                continue
            result.append(ch)
        return ''.join(result)

    s = _fix_newlines_in_strings(s)

    return s


def _try_json_repair(s: str):
    """Last-resort JSON repair using json-repair library if available.

    Returns parsed dict/list or raises the original error.
    """
    try:
        from json_repair import repair_json
        repaired = repair_json(s, return_objects=True)
        if isinstance(repaired, (dict, list)):
            return repaired
        # repair_json with return_objects=False returns a string
        repaired_str = repair_json(s, return_objects=False)
        return _json.loads(repaired_str)
    except ImportError:
        raise _json.JSONDecodeError("json-repair not installed", s[:50], 0)
    except Exception:
        raise _json.JSONDecodeError("json-repair failed", s[:50], 0)


def _dashi_script_sync(script_name: str, args: str = "") -> dict:
    """Run the one read-only script exposed to the Agent: layout-query.mjs.

    Layout inspection, scaffold creation, safe writes, rendering, and exports are
    intentionally available only through their typed wrapper tools.  Keeping this
    boundary prevents the Agent from reviving the legacy inspect -> handwritten
    goal.json workflow.
    """
    if script_name != "layout-query.mjs":
        return {
            "error": (
                "Only layout-query.mjs is exposed as a read-only selection step. "
                "Use dashi_scaffold, dashi_stage_media, dashi_write_goal, and "
                "dashi_render for inspection, media staging, writes, rendering, "
                "and exports."
            )
        }

    root, proj = _dashi_paths()
    os.environ["DASHI_PPT_PROJECT_ROOT"] = proj
    os.environ["DASHI_PPT_SKILL_ROOT"] = root
    os.environ["DASHI_PPT_THEME_RUNTIME"] = os.environ.get("DASHI_PPT_THEME_RUNTIME", "prebuilt")
    node = shutil.which("node") or "node"
    ps = shutil.which("powershell") or "C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    ext = script_name.rsplit(".", 1)[-1].lower() if "." in script_name else ""
    script_path = os.path.join(proj, "scripts", script_name)
    if not os.path.exists(script_path):
        script_path = os.path.join(root, "scripts", script_name)
    if not os.path.exists(script_path):
        return {"error": f"Script not found: {script_name}"}
    if ext in ("mjs", "cjs", "js", "jsx"):
        cmd = [node, script_path]
    elif ext == "ps1":
        cmd = [ps, "-ExecutionPolicy", "Bypass", "-File", script_path]
    else:
        return {"error": f"Unsupported script type: .{ext}"}
    if args.strip():
        try:
            cmd.extend(shlex.split(args.strip()))
        except ValueError as exc:
            return {"error": f"Invalid layout-query args: {exc}"}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120, cwd=proj)
    stdout = r.stdout or ""
    stderr = r.stderr or ""
    stdout = _filter_layout_query(stdout)
    return {"stdout": stdout, "stderr": stderr, "returncode": r.returncode}


async def dashi_script(script_name: str, args: str = "") -> dict:
    """Query layout candidates without writing files.

    The only allowed script is ``layout-query.mjs``. Select one cover plus the
    required number of unique body layouts, then pass those exact IDs to
    ``dashi_scaffold(layouts=[...])``. Inspection and every mutating operation
    stay encapsulated by the typed scaffold/stage/write/render tools.

    Usage: dashi_script(
        script_name="layout-query.mjs",
        args="--theme theme07 --role content --limit 15"
    )
    """
    return await asyncio.to_thread(_dashi_script_sync, script_name, args)


def _dashi_stage_media_sync(output_dir: str, media_paths: list[str]) -> dict:
    """Stage uploaded media into one confined deck output directory."""
    _root, proj = _dashi_paths()
    if not isinstance(media_paths, list) or not media_paths:
        return {"error": "media_paths must be a non-empty list"}
    if len(media_paths) > 20:
        return {"error": "at most 20 media files can be staged at once"}
    if not isinstance(output_dir, str) or output_dir.lower().endswith((".json", ".html", ".htm")):
        return {"error": "output_dir must be a deck directory such as output/my-deck"}

    try:
        output_rel, _ = _resolve_project_output_path(proj, output_dir, suffix="")
        sources = [_resolve_media_source(proj, item) for item in media_paths]
    except ValueError as exc:
        return {"error": f"Invalid media staging path: {exc}"}

    stage_script = os.path.join(proj, "scripts", "stage-media.mjs")
    if not os.path.isfile(stage_script):
        return {"error": f"Media staging script not found: {stage_script}"}

    node = shutil.which("node") or "node"
    result = subprocess.run(
        [node, stage_script, output_rel, *sources],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        cwd=proj,
        env=_project_subprocess_env(proj),
    )
    if result.returncode != 0:
        return {
            "error": (result.stderr or result.stdout or "Media staging failed.")[-1200:],
            "returncode": result.returncode,
        }
    try:
        payload = _json.loads(result.stdout)
    except _json.JSONDecodeError:
        return {
            "error": "Media staging returned invalid JSON.",
            "returncode": result.returncode,
        }
    items = [
        {
            key: item[key]
            for key in ("relative", "kind", "mime", "convertedFrom")
            if key in item
        }
        for item in (payload.get("items") or [])
        if isinstance(item, dict)
    ]
    return {
        "output_dir": output_rel,
        "items": items,
        "returncode": result.returncode,
    }


async def dashi_stage_media(output_dir: str, media_paths: list[str]) -> dict:
    """Copy uploaded images/videos into a deck and return safe relative paths.

    ``output_dir`` must stay under project ``output/``. Every source must be an
    existing file under the server ``uploads/`` directory or another confined
    deck output. Use each returned ``items[].relative`` value exactly once in
    the media field advertised by
    ``dashi_scaffold.slides_spec[].fill_plan.media``.
    """
    return await asyncio.to_thread(_dashi_stage_media_sync, output_dir, media_paths)


def _dashi_render_sync(goal_path: str, output_html: str = "", export_pptx: bool = True) -> dict:
    """Synchronous implementation of dashi_render."""
    root, proj = _dashi_paths()
    os.environ["DASHI_PPT_PROJECT_ROOT"] = proj
    os.environ["DASHI_PPT_SKILL_ROOT"] = root
    os.environ["DASHI_PPT_THEME_RUNTIME"] = os.environ.get("DASHI_PPT_THEME_RUNTIME", "prebuilt")
    
    # Ensure OPENSSL_PATH is set for PPTX export
    openssl_paths = [
        r"C:\Program Files\OpenSSL-Win64\bin\openssl.exe",
        r"C:\Program Files\Git\mingw64\bin\openssl.exe",
        r"C:\Program Files\Git\usr\bin\openssl.exe",
    ]
    for op in openssl_paths:
        if os.path.exists(op):
            os.environ["OPENSSL_PATH"] = op
            break
    
    node = shutil.which("node") or "node"
    npm = shutil.which("npm") or "npm"
    subprocess_env = _project_subprocess_env(proj)
    
    # Resolve all caller-controlled paths before creating directories or running
    # scripts. Only project/output artifacts are valid tool inputs.
    try:
        goal_rel, _goal_full = _resolve_project_output_path(proj, goal_path, suffix=".json")
    except ValueError as exc:
        return {
            "error": f"Invalid goal_path: {exc}",
            "validation_failed": True,
            "html_exists": False,
        }
    if not output_html:
        deck_dir = os.path.dirname(goal_rel)
        output_html = os.path.join(deck_dir, "ppt", "index.html").replace("\\", "/")
    try:
        html_rel, html_full = _resolve_project_output_path(proj, output_html, suffix=".html")
    except ValueError as exc:
        return {
            "error": f"Invalid output_html: {exc}",
            "validation_failed": True,
            "html_exists": False,
        }
    output_dir = os.path.dirname(html_full)
    html_public_rel = html_rel
    
    results = {}
    
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Step 0: Run write-safe-props to validate and normalize goal.json.
    # Rendering/exporting must never proceed from an invalid goal or reuse a
    # stale artifact that happened to be left by an earlier successful run.
    write_safe_script = os.path.join(proj, "scripts", "write-safe-props.mjs")
    stale_html_exists = os.path.exists(html_full)
    if not os.path.exists(write_safe_script):
        results["validation_failed"] = True
        results["validation_errors"] = f"Pre-render validator not found: {write_safe_script}"
    else:
        ws_cmd = [node, write_safe_script, "--goal", goal_rel, "--write"]
        try:
            ws_r = subprocess.run(
                ws_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
                cwd=proj,
                env=subprocess_env,
            )
        except Exception as exc:
            results["validate_props"] = {
                "stdout": "",
                "stderr": str(exc),
                "returncode": -1,
            }
            results["validation_failed"] = True
            results["validation_errors"] = f"Goal pre-validation failed: {exc}"
        else:
            ws_stdout = ws_r.stdout.strip() if ws_r.stdout else ""
            ws_stderr = ws_r.stderr.strip() if ws_r.stderr else ""
            results["validate_props"] = {
                "stdout": ws_stdout[-800:] if ws_stdout else "",
                "stderr": ws_stderr[-1000:] if ws_stderr else "",
                "returncode": ws_r.returncode,
            }
            if ws_r.returncode != 0:
                results["validation_failed"] = True
                results["validation_errors"] = (ws_stderr or ws_stdout or "Goal validation failed.")[-1200:]

    if results.get("validation_failed"):
        results["fix_hint"] = (
            "Goal validation failed. Correct prop names, nested structure, types "
            "(string vs array vs object), maxChars limits, and array counts according "
            "to the fill_plan returned by dashi_scaffold. Then update goal.json with "
            "dashi_write_goal and re-render."
        )
        results["html_path"] = html_full
        results["html_exists"] = False
        if stale_html_exists:
            results["stale_html_ignored"] = True
        return results

    # Step 1: Render using tsx directly (avoid npm shell escaping on Linux)
    tsx_name = "tsx.cmd" if os.name == "nt" else "tsx"
    tsx_bin = os.path.join(proj, "node_modules", ".bin", tsx_name)
    render_script = os.path.join(proj, "scripts", "render-goal-deck.jsx")
    if os.name == "nt":
        cmd = ["cmd", "/c", tsx_bin, render_script, goal_rel, html_rel]
    else:
        cmd = [node, tsx_bin, render_script, goal_rel, html_rel]
    r = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        timeout=300, cwd=proj, env=subprocess_env,
    )
    results["render"] = {
        "stdout": r.stdout[-800:] if r.stdout else "",
        "stderr": r.stderr[-800:] if r.stderr else "",
        "returncode": r.returncode,
    }
    
    # Step 2: Validate if render succeeded
    if r.returncode != 0:
        results["html_path"] = html_full
        results["html_exists"] = False
        if stale_html_exists:
            results["stale_html_ignored"] = True
        return results

    validate_script = os.path.join(proj, "scripts", "validate-swiss-deck.mjs")
    if not os.path.exists(validate_script):
        results["validation_failed"] = True
        results["validation_errors"] = f"Rendered HTML validator not found: {validate_script}"
        results["html_path"] = html_full
        results["html_exists"] = False
        return results
    cmd = [node, validate_script, html_rel]
    r2 = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8",
        timeout=60, cwd=proj, env=subprocess_env,
    )
    results["validate"] = {
        "stdout": r2.stdout[-500:] if r2.stdout else "",
        "stderr": r2.stderr[-500:] if r2.stderr else "",
        "returncode": r2.returncode,
    }
    if r2.returncode != 0:
        results["validation_failed"] = True
        results["validation_errors"] = (r2.stderr or r2.stdout or "Rendered HTML validation failed.")[-1200:]
        results["html_path"] = html_full
        results["html_exists"] = False
        return results
    
    # Step 3: Export PPTX — pass the ppt/ subdir (where index.html lives)
    if export_pptx and os.path.exists(html_full):
        try:
            deck_ppt_abs = output_dir
            export_script = os.path.join(proj, "scripts", "export-pptx.mjs")
            if os.path.exists(export_script):
                cmd = [node, export_script, deck_ppt_abs]
                r3 = subprocess.run(
                    cmd, capture_output=True, text=True, encoding="utf-8",
                    timeout=120, cwd=proj, env=subprocess_env,
                )
                deck_name = os.path.basename(os.path.dirname(goal_rel))
                pptx_path = os.path.join(deck_ppt_abs, f"{deck_name}.pptx")
                results["export_pptx"] = {
                    "stdout": r3.stdout[-800:] if r3.stdout else "",
                    "stderr": r3.stderr[-800:] if r3.stderr else "",
                    "returncode": r3.returncode,
                    "pptx_path": pptx_path,
                    "pptx_exists": r3.returncode == 0 and os.path.exists(pptx_path),
                }
                pptx_public_rel = _project_relative_path(proj, pptx_path)
                if results["export_pptx"]["pptx_exists"] and pptx_public_rel:
                    results["download_url"] = _artifact_url("download", pptx_public_rel)
                    # Copy PPTX to user uploads for workspace access
                    workspace_path = _copy_pptx_to_uploads(pptx_path, deck_name)
                    if workspace_path:
                        results["workspace_path"] = workspace_path
        except Exception as e:
            results["export_pptx"] = {"error": str(e), "returncode": -1}
    
    results["html_path"] = html_full
    results["html_exists"] = os.path.exists(html_full)
    if results["html_exists"] and html_public_rel:
        results["preview_url"] = _artifact_url("preview", html_public_rel)
    return results


async def dashi_render(goal_path: str, output_html: str = "", export_pptx: bool = True) -> dict:
    """Render dashi-ppt goal.json to HTML and optionally export PPTX (async, non-blocking).
    
    Usage: dashi_render(goal_path="output/mydeck/goal.json")"""
    return await asyncio.to_thread(_dashi_render_sync, goal_path, output_html, export_pptx)


def dashi_write_goal(goal_path: str, goal_data) -> dict:
    """Write a complete goal.json with all page content (props) filled in.

    goal_data can be a dict or a JSON string.
    Use this after dashi_scaffold and follow its returned fill_plan exactly.

    The goal_data must have this structure:
    {
      "title": "Presentation Title",
      "goal": "Description of the presentation goal",
      "themePack": "theme07",
      "pageCount": N,
      "slides": [
        {"layout": "theme07_page005", "props": {"titleL1": "Title", "titleL2Em": "Subtitle", ...}},
        ...
      ]
    }

    After writing, runs validate:goal-spec to catch issues early.

    Smart behavior:
      - If disk already has a typed goal.json (from dashi_scaffold), merges user
        props onto the typed skeleton rather than overwriting.
      - JSON string parsing falls back to _sanitize_json_string retry.

    Usage: dashi_write_goal(goal_path="output/agent_intro/goal.json", goal_data={...})
    """
    root, proj = _dashi_paths()
    try:
        goal_rel, full_path = _resolve_project_output_path(proj, goal_path, suffix=".json")
    except ValueError as exc:
        return {"error": f"Invalid goal_path: {exc}"}
    subprocess_env = _project_subprocess_env(proj)

    # Handle goal_data as JSON string
    if isinstance(goal_data, str):
        try:
            goal_data = _json.loads(goal_data)
        except _json.JSONDecodeError as e:
            # Try sanitization pass to recover from common LLM JSON bugs
            try:
                cleaned = _sanitize_json_string(goal_data)
                goal_data = _json.loads(cleaned)
            except _json.JSONDecodeError as e2:
                # Third attempt: json-repair library (handles structural damage)
                try:
                    goal_data = _try_json_repair(goal_data)
                    _log.info("goal_data recovered via json-repair")
                except (_json.JSONDecodeError, Exception):
                    # All recovery attempts failed — provide detailed error
                    pos = e2.pos
                    snippet_start = max(0, pos - 100)
                    snippet_end = min(len(goal_data), pos + 100)
                    snippet = goal_data[snippet_start:snippet_end]
                    error_context = (
                        f"goal_data JSON parse error at position {pos}: {e2.msg}\n"
                        f"Context around position {pos}:\n"
                        f"'{snippet}'\n"
                        f"Note: Ensure all string values use proper escaping and quotes.\n"
                        f"Common issues:\n"
                        f"  - Unescaped quotes inside strings (use \\')\n"
                        f"  - Single quotes used instead of double quotes\n"
                        f"  - Number values written as strings (e.g., '123' instead of 123)\n"
                        f"  - Missing commas between array/object elements\n"
                        f"  - Trailing commas (auto-stripped, but other issues remain)\n"
                        f"TIP: For large decks, use dashi_fill_slide() to fill one slide at a time."
                    )
                    return {"error": error_context}
    elif not isinstance(goal_data, dict):
        return {"error": f"goal_data must be a dict or JSON string, got {type(goal_data).__name__}"}

    # Ensure parent directory exists
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    # Validate basic structure
    if "slides" not in goal_data or not isinstance(goal_data["slides"], list):
        return {"error": "goal_data must have a 'slides' array"}

    # Smart merge: if disk already has a typed goal.json (from dashi_scaffold),
    # merge user-provided values onto the typed skeleton instead of overwriting types.
    existing_template = None
    if os.path.exists(full_path):
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                existing_template = _json.load(f)
            if not isinstance(existing_template, dict) or not isinstance(existing_template.get("slides"), list):
                existing_template = None
        except (OSError, _json.JSONDecodeError):
            existing_template = None

    if existing_template:
        # Merge slides index by index; user slides win for keys they provide.
        ex_slides = existing_template["slides"]
        new_slides = goal_data["slides"]
        # Pad/align lengths
        if len(new_slides) < len(ex_slides):
            # Fill missing with template slides
            new_slides = list(new_slides) + ex_slides[len(new_slides):]
        for idx, new_s in enumerate(new_slides):
            if idx >= len(ex_slides):
                break
            old_s = ex_slides[idx]
            if not isinstance(new_s, dict) or not isinstance(old_s, dict):
                continue
            # Layout mismatch warning (keep old layout)
            if new_s.get("layout") and old_s.get("layout") and new_s["layout"] != old_s["layout"]:
                _log.warning("write_goal: slide %d layout mismatch user=%s template=%s, keeping template",
                             idx + 1, new_s.get("layout"), old_s.get("layout"))
                new_s["layout"] = old_s["layout"]
            elif not new_s.get("layout"):
                new_s["layout"] = old_s.get("layout", "")
            # Merge props: template provides typed skeleton, user fills values
            old_props = old_s.get("props", {}) if isinstance(old_s.get("props"), dict) else {}
            new_props = new_s.get("props", {}) if isinstance(new_s.get("props"), dict) else {}
            new_s["props"] = _merge_prop_skeleton(old_props, new_props)
        goal_data["slides"] = new_slides
        # Preserve top-level fields from template if missing
        for k in ("title", "goal", "themePack", "pageCount", "audience", "owner", "randomSeed"):
            if not goal_data.get(k) and existing_template.get(k):
                goal_data[k] = existing_template[k]

    slides = goal_data["slides"]
    filled = sum(
        1
        for s in slides
        if isinstance(s, dict) and isinstance(s.get("props"), dict) and len(s.get("props", {})) > 0
    )
    empty = len(slides) - filled

    # --- Array padding: fill each slide's array fields up to count, and set countKey ---
    # This protects against LLM-produced arrays that are too short or missing count keys.
    try:
        node = shutil.which("node") or "node"
        inspect_script = os.path.join(proj, "scripts", "inspect-layout.mjs")
        if os.path.exists(inspect_script):
            unique_layouts = []
            seen_layouts = set()
            for s in slides:
                if not isinstance(s, dict):
                    continue
                ly = s.get("layout")
                if ly and ly not in seen_layouts:
                    seen_layouts.add(ly)
                    unique_layouts.append(ly)
            layout_to_arrays = {}
            for li in range(0, len(unique_layouts), 8):
                batch_l = unique_layouts[li:li + 8]
                icmd = [node, inspect_script]
                for ly in batch_l:
                    icmd.extend(["--layout", ly])
                ir = subprocess.run(
                    icmd, capture_output=True, text=True, encoding="utf-8",
                    timeout=120, cwd=proj, env=subprocess_env,
                )
                istdout = ir.stdout or ""
                try:
                    iresults = _inspect_layout_items(istdout) if istdout.strip() else []
                except _json.JSONDecodeError:
                    iresults = []
                for ires in iresults:
                    fill_plan = ires.get("fillPlan") or {}
                    layout_to_arrays[ires.get("layout")] = fill_plan.get("arrays") or []

            for s in slides:
                if not isinstance(s, dict):
                    continue
                props = s.get("props")
                if not isinstance(props, dict):
                    continue
                ly = s.get("layout")
                array_plans = layout_to_arrays.get(ly) or []
                for plan in array_plans:
                    key = plan.get("key", "")
                    if not _prop_path_tokens(key):
                        _log.warning("ignored unsupported fillPlan array path: %s", key)
                        continue

                    visible_count = plan.get("visibleCount")
                    default_count = plan.get("defaultCount")
                    max_count = plan.get("maxCount")
                    ck = plan.get("countKey", "")
                    array_targets = _prop_leaf_targets(props, key, create=True)
                    if not array_targets:
                        _log.warning("fillPlan array path had no writable targets: %s", key)
                        continue
                    count_targets = _prop_leaf_targets(props, ck, create=True) if ck else []
                    item_shape = plan.get("itemShape")

                    for target_index, (array_parent, array_key) in enumerate(array_targets):
                        count_target = None
                        if len(count_targets) == len(array_targets):
                            count_target = count_targets[target_index]
                        elif len(count_targets) == 1:
                            count_target = count_targets[0]

                        current_count = (
                            count_target[0].get(count_target[1], _MISSING)
                            if count_target
                            else _MISSING
                        )
                        authored_count = current_count
                        if isinstance(authored_count, str):
                            try:
                                authored_count = int(authored_count)
                            except ValueError:
                                authored_count = None
                        if authored_count is _MISSING:
                            authored_count = None

                        target_cnt = authored_count if _is_json_number(authored_count) else visible_count
                        if not _is_json_number(target_cnt):
                            target_cnt = default_count
                        if not _is_json_number(target_cnt):
                            target_cnt = 0
                        target_cnt = max(0, int(target_cnt))
                        target_max = max_count if _is_json_number(max_count) else target_cnt
                        target_max = max(0, int(target_max))

                        arr = array_parent.get(array_key)
                        if not isinstance(arr, list):
                            # fillPlan.arrays is authoritative: this field is an array.
                            arr = []
                            array_parent[array_key] = arr
                        if len(arr) < target_cnt and item_shape is not None:
                            import copy as _copy
                            placeholder = _empty_value_for_shape(item_shape)
                            for _ in range(target_cnt - len(arr)):
                                arr.append(_copy.deepcopy(placeholder))
                        if len(arr) > target_max:
                            arr = arr[:target_max]
                            array_parent[array_key] = arr

                        if count_target:
                            count_parent, count_key = count_target
                            if current_count is _MISSING:
                                count_parent[count_key] = len(arr)
                            elif isinstance(current_count, str) and _is_json_number(authored_count):
                                count_parent[count_key] = int(authored_count)
                            elif not _is_json_number(current_count):
                                try:
                                    count_parent[count_key] = int(current_count)
                                except (ValueError, TypeError):
                                    count_parent[count_key] = len(arr)
    except Exception as _e:
        _log.warning("array padding failed: %s", _e)

    # Detailed slide inspection
    slide_details = []
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            slide_details.append({
                "idx": i,
                "layout": "?",
                "prop_count": 0,
                "closing_hollow": False,
                "closing_val": "(missing)",
                "hollow_fields": [],
            })
            continue
        p = s.get("props", {}) if isinstance(s.get("props"), dict) else {}
        prop_count = len(p)
        # Detect hollow content across ALL text props (not just closing)
        hollow_keywords = ["了解", "见证", "展望", "探索", "开启", "追踪", "发现", "理解", "关注", "聚焦", "洞察", "引领", "驱动", "赋能", "重构", "重塑", "颠覆", "拥抱", "迈向", "把握", "深入", "解读", "突破", "创新", "变革"]
        hollow = False
        closing_val = p.get("closing", "")
        hollow_fields = []
        for field_name in ["closing", "lead", "sublead", "body", "note", "title", "subtitle"]:
            field_val = p.get(field_name, "")
            if field_val and isinstance(field_val, str) and len(field_val) < 30:
                if any(kw in field_val for kw in hollow_keywords):
                    hollow = True
                    hollow_fields.append(field_name)

        slide_details.append({
            "idx": i,
            "layout": s.get("layout", "?"),
            "prop_count": prop_count,
            "closing_hollow": hollow,
            "closing_val": closing_val[:60] if closing_val else "(missing)",
            "hollow_fields": hollow_fields
        })
    
    # Detect nested object format issues (dot notation vs object notation)
    nested_issues = []
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            continue
        p = s.get("props", {}) if isinstance(s.get("props"), dict) else {}
        for key in list(p.keys()):
            if "." in key:
                # Found dot notation - this is a nested object format error
                base = key.split(".")[0]
                nested_issues.append({
                    "slide": i + 1,
                    "wrong_key": key,
                    "correct_format": base,
                    "hint": f'Use nested object: "{base}": {{"sub": value}} instead of flat "{key}": value'
                })
    
    # Validate a same-directory temporary candidate. The published goal is only
    # replaced after validation succeeds, so a bad write can never destroy the
    # last known-good goal.json.
    validation_ok = False
    validation_output = ""
    auto_fixes = []
    candidate_path = None
    try:
        fd, candidate_path = tempfile.mkstemp(
            prefix=f".{os.path.basename(full_path)}.",
            suffix=".tmp",
            dir=os.path.dirname(full_path),
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _json.dump(goal_data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())

        node = shutil.which("node") or "node"
        validate_script = os.path.join(proj, "scripts", "validate-goal-spec.mjs")
        if not os.path.exists(validate_script):
            raise FileNotFoundError(f"goal validator not found: {validate_script}")

        for attempt in range(3):
            r = subprocess.run(
                [node, validate_script, candidate_path],
                capture_output=True, text=True, encoding="utf-8", timeout=60,
                cwd=proj, env=subprocess_env,
            )
            validation_output = (r.stdout or "") + (r.stderr or "")
            validation_ok = r.returncode == 0
            if validation_ok:
                break
            # Parse and auto-fix errors
            stderr_text = r.stderr or ""
            fixes_this_round = 0
            for line in stderr_text.split("\n"):
                # Text too long: "props.field: compact copy is too long (actual > max)"
                m = re.search(r"props\.(\S+?):.*?too long \(.*?> (\d+)\)", line)
                if m:
                    field_path = m.group(1)
                    limit = int(m.group(2))
                    for si, slide in enumerate(slides):
                        props = slide.get("props", {})
                        if field_path in props:
                            val = props[field_path]
                            if isinstance(val, str) and len(val) > limit:
                                props[field_path] = val[:limit]
                                fixes_this_round += 1
                                auto_fixes.append(f"slide {si+1} {field_path}: truncated {len(val)} -> {limit}")
                            break
                    continue
                # Number expected: "props.field[index]: expected number"
                m = re.search(r"props\.(\S+?): expected number", line)
                if m:
                    field_path = m.group(1)
                    m2 = re.match(r"(\w+)\[(\d+)\](?:\.(\w+))?", field_path)
                    if m2:
                        base = m2.group(1)
                        idx = int(m2.group(2))
                        subfield = m2.group(3)
                        for si, slide in enumerate(slides):
                            props = slide.get("props", {})
                            if base in props and isinstance(props[base], list):
                                arr = props[base]
                                if idx < len(arr):
                                    if subfield and isinstance(arr[idx], dict):
                                        val = arr[idx].get(subfield)
                                        if isinstance(val, str):
                                            try:
                                                arr[idx][subfield] = float(val) if "." in val else int(val)
                                                fixes_this_round += 1
                                            except ValueError:
                                                pass
                                    elif not subfield:
                                        val = arr[idx]
                                        if isinstance(val, str):
                                            try:
                                                arr[idx] = float(val) if "." in val else int(val)
                                                fixes_this_round += 1
                                            except ValueError:
                                                pass
                            break
                # --- NEW: Top-level field "expected number" (not in array) ---
                m = re.search(r"props\.(\w+): expected number", line)
                if m:
                    field_name = m.group(1)
                    for si, slide in enumerate(slides):
                        props = slide.get("props", {})
                        if field_name in props:
                            val = props[field_name]
                            if isinstance(val, str):
                                try:
                                    props[field_name] = float(val) if "." in val else int(val)
                                    fixes_this_round += 1
                                    auto_fixes.append("slide %d %s: string->number" % (si+1, field_name))
                                except ValueError:
                                    pass
                            break
                    continue
                # --- NEW: Top-level field "expected string, got number" ---
                m = re.search(r"props\.(\w+): expected string", line)
                if m:
                    field_name = m.group(1)
                    for si, slide in enumerate(slides):
                        props = slide.get("props", {})
                        if field_name in props and not isinstance(props[field_name], str):
                            props[field_name] = str(props[field_name])
                            fixes_this_round += 1
                            auto_fixes.append("slide %d %s: number->string" % (si+1, field_name))
                            break
                    continue
                # --- NEW: Nested array field too long (conclusions[0].note) ---
                m = re.search(r"props\.(\w+)\[(\d+)\]\.(\w+):.*?too long \(.*?> (\d+)\)", line)
                if m:
                    base, idx, sub, limit = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
                    for si, slide in enumerate(slides):
                        props = slide.get("props", {})
                        if base in props and isinstance(props[base], list):
                            arr = props[base]
                            if idx < len(arr) and isinstance(arr[idx], dict) and sub in arr[idx]:
                                val = arr[idx][sub]
                                if isinstance(val, str) and len(val) > limit:
                                    arr[idx][sub] = val[:limit]
                                    fixes_this_round += 1
                                    auto_fixes.append("slide %d %s[%d].%s: truncated %d -> %d" % (si+1, base, idx, sub, len(val), limit))
                            break
                    continue
                # --- NEW: Value out of range ---
                m = re.search(r"props\.(\S+?):.*?(?:out of|exceeds|range|expected)[^0-9]*?(\d+(?:\.\d+)?)[^0-9]*?(\d+(?:\.\d+)?)", line)
                if m:
                    field_path, lo, hi = m.group(1), float(m.group(2)), float(m.group(3))
                    if lo > hi:
                        lo, hi = hi, lo
                    m2 = re.match(r"(\w+)\[(\d+)\]\.(\w+)", field_path)
                    if m2:
                        base, idx, sub = m2.group(1), int(m2.group(2)), m2.group(3)
                        for si, slide in enumerate(slides):
                            props = slide.get("props", {})
                            if base in props and isinstance(props[base], list):
                                arr = props[base]
                                if idx < len(arr) and isinstance(arr[idx], dict) and sub in arr[idx]:
                                    val = arr[idx][sub]
                                    if isinstance(val, (int, float)):
                                        clamped = max(lo, min(hi, val))
                                        if clamped != val:
                                            arr[idx][sub] = clamped
                                            fixes_this_round += 1
                                            auto_fixes.append("slide %d %s[%d].%s: clamped %s -> %s [%.1f,%.1f]" % (si+1, base, idx, sub, str(val), str(clamped), lo, hi))
                                break
                    continue
            if fixes_this_round > 0:
                with open(candidate_path, "w", encoding="utf-8") as f:
                    _json.dump(goal_data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                auto_fixes.append(f"Round {attempt+1}: {fixes_this_round} fixes")
            else:
                break

        if validation_ok:
            os.replace(candidate_path, full_path)
            candidate_path = None
    except Exception as e:
        validation_ok = False
        validation_output = f"Goal validation failed: {str(e)[:300]}"
    finally:
        if candidate_path and os.path.exists(candidate_path):
            try:
                os.unlink(candidate_path)
            except OSError:
                _log.warning("failed to remove rejected goal candidate: %s", candidate_path)

    
    result = {
        "auto_fixes": auto_fixes,
        "goal_path": goal_rel,
        "slide_count": len(slides),
        "slides_with_content": filled,
        "slides_empty": empty,
        "layouts": [s.get("layout", "?") if isinstance(s, dict) else "?" for s in slides],
        "slide_details": slide_details,
        "validation_passed": validation_ok,
        "validation_output": validation_output,
    }
    if validation_ok:
        result["written"] = full_path
    else:
        result["error"] = "Goal spec validation failed; existing goal.json was not changed."
    
    if empty > 0:
        result["WARNING"] = f"{empty} slides have EMPTY props! Template default text will appear. Re-run dashi_write_goal with ALL props filled."
    
    hollow_slides = [d for d in slide_details if d.get("closing_hollow")]
    if hollow_slides:
        result["WARNING_HOLLOW"] = f"{len(hollow_slides)} slides have hollow closing content! Replace with specific facts (model names, data, dates, benchmarks). Slides: {[d['idx'] for d in hollow_slides]}"
    
    if nested_issues:
        result["ERROR_NESTED_OBJECT"] = (
            f"Found {len(nested_issues)} nested object format errors! "
            "Use nested object format {sub: value} instead of dot notation sub.value. "
            f"Issues: " + "; ".join([f"slide {i['slide']} {i['wrong_key']} → {i['correct_format']}" for i in nested_issues[:5]])
        )
    
    if not validation_ok:
        result["WARNING_VALIDATION"] = f"Goal spec validation FAILED. Fix errors before rendering: {validation_output[:300]}"
    
    return result


def dashi_fill_slide(goal_path: str, slide_index: int, props, finalize: bool = False) -> dict:
    """Incrementally fill one slide's props into an existing scaffold goal.json.

    This is the RECOMMENDED approach for large decks (5+ slides) to avoid
    JSON serialization errors when the model outputs large Chinese content.

    Workflow:
      1. dashi_scaffold(...) -> writes typed skeleton to disk
      2. dashi_fill_slide(goal_path, slide_index=0, props={...})  # fill slide 1
      3. dashi_fill_slide(goal_path, slide_index=1, props={...})  # fill slide 2
      ...
      N. dashi_fill_slide(goal_path, slide_index=0, props={}, finalize=True)  # validate & publish

    Args:
        goal_path: Relative path to goal.json (from dashi_scaffold output)
        slide_index: 0-based index of the slide to fill
        props: Dict (or JSON string) of props to merge onto the slide's skeleton.
               Only provided keys are updated; skeleton types are preserved.
        finalize: If True, run full validation (validate:goal-spec) after merging.
                  Set this on your LAST call to publish the validated goal.

    Returns:
        {"filled": slide_index, "layout": "...", "prop_count": N, ...}
        When finalize=True, also includes validation_passed, validation_output, etc.
    """
    root, proj = _dashi_paths()
    try:
        goal_rel, full_path = _resolve_project_output_path(proj, goal_path, suffix=".json")
    except ValueError as exc:
        return {"error": f"Invalid goal_path: {exc}"}

    # Load existing goal.json from disk (must exist from dashi_scaffold)
    if not os.path.exists(full_path):
        return {"error": f"goal.json not found at {goal_rel}. Run dashi_scaffold first."}
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            goal_data = _json.load(f)
    except (OSError, _json.JSONDecodeError) as e:
        return {"error": f"Failed to read existing goal.json: {e}"}

    slides = goal_data.get("slides")
    if not isinstance(slides, list) or not slides:
        return {"error": "goal.json has no slides array. Run dashi_scaffold first."}

    # Parse props if string
    if isinstance(props, str):
        try:
            props = _json.loads(props)
        except _json.JSONDecodeError:
            try:
                cleaned = _sanitize_json_string(props)
                props = _json.loads(cleaned)
            except _json.JSONDecodeError:
                try:
                    props = _try_json_repair(props)
                except Exception:
                    return {"error": f"props JSON parse failed. Ensure valid JSON object."}
    if not isinstance(props, dict):
        return {"error": f"props must be a dict or JSON string, got {type(props).__name__}"}

    # Validate slide_index
    if not isinstance(slide_index, int) or isinstance(slide_index, bool):
        return {"error": "slide_index must be an integer (0-based)"}
    if slide_index < 0 or slide_index >= len(slides):
        return {"error": f"slide_index {slide_index} out of range [0, {len(slides)-1}]"}

    # Merge props onto the slide's typed skeleton
    slide = slides[slide_index]
    if not isinstance(slide, dict):
        slide = {"layout": "", "props": {}}
        slides[slide_index] = slide

    old_props = slide.get("props", {}) if isinstance(slide.get("props"), dict) else {}
    if props:  # Only merge if props is non-empty
        slide["props"] = _merge_prop_skeleton(old_props, props)

    # Save back to disk (atomic write, no validation yet unless finalize)
    _atomic_write_json(full_path, goal_data)

    result = {
        "filled": slide_index,
        "layout": slide.get("layout", "?"),
        "prop_count": len(slide.get("props", {})),
        "total_slides": len(slides),
        "goal_path": goal_rel,
    }

    # Count how many slides have non-empty props
    filled_count = sum(
        1 for s in slides
        if isinstance(s, dict) and isinstance(s.get("props"), dict) and len(s.get("props", {})) > 0
    )
    result["slides_with_content"] = filled_count
    result["slides_remaining"] = len(slides) - filled_count

    if finalize:
        # Run full validation pipeline (same as dashi_write_goal)
        subprocess_env = _project_subprocess_env(proj)
        validation_ok = False
        validation_output = ""
        auto_fixes = []
        candidate_path = None
        try:
            fd, candidate_path = tempfile.mkstemp(
                prefix=f".{os.path.basename(full_path)}.",
                suffix=".tmp",
                dir=os.path.dirname(full_path),
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                _json.dump(goal_data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())

            node = shutil.which("node") or "node"
            validate_script = os.path.join(proj, "scripts", "validate-goal-spec.mjs")
            if not os.path.exists(validate_script):
                raise FileNotFoundError(f"goal validator not found: {validate_script}")

            for attempt in range(3):
                r = subprocess.run(
                    [node, validate_script, candidate_path],
                    capture_output=True, text=True, encoding="utf-8", timeout=60,
                    cwd=proj, env=subprocess_env,
                )
                validation_output = (r.stdout or "") + (r.stderr or "")
                validation_ok = r.returncode == 0
                if validation_ok:
                    break
                # Parse and auto-fix errors (reuse same logic as dashi_write_goal)
                stderr_text = r.stderr or ""
                fixes_this_round = 0
                for line in stderr_text.split("\n"):
                    m = re.search(r"props\.(\S+?):.*?too long \(.*?> (\d+)\)", line)
                    if m:
                        field_path = m.group(1)
                        limit = int(m.group(2))
                        for si, sl in enumerate(slides):
                            sl_props = sl.get("props", {})
                            if field_path in sl_props:
                                val = sl_props[field_path]
                                if isinstance(val, str) and len(val) > limit:
                                    sl_props[field_path] = val[:limit]
                                    fixes_this_round += 1
                                    auto_fixes.append(f"slide {si+1} {field_path}: truncated {len(val)} -> {limit}")
                                break
                        continue
                    m = re.search(r"props\.(\w+)\[(\d+)\]\.(\w+):.*?too long \(.*?> (\d+)\)", line)
                    if m:
                        base, idx, sub, limit = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
                        for si, sl in enumerate(slides):
                            sl_props = sl.get("props", {})
                            if base in sl_props and isinstance(sl_props[base], list):
                                arr = sl_props[base]
                                if idx < len(arr) and isinstance(arr[idx], dict) and sub in arr[idx]:
                                    val = arr[idx][sub]
                                    if isinstance(val, str) and len(val) > limit:
                                        arr[idx][sub] = val[:limit]
                                        fixes_this_round += 1
                                        auto_fixes.append(f"slide {si+1} {base}[{idx}].{sub}: truncated")
                                break
                        continue
                if fixes_this_round > 0:
                    with open(candidate_path, "w", encoding="utf-8") as f:
                        _json.dump(goal_data, f, ensure_ascii=False, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    auto_fixes.append(f"Round {attempt+1}: {fixes_this_round} fixes")
                else:
                    break

            if validation_ok:
                os.replace(candidate_path, full_path)
                candidate_path = None
        except Exception as e:
            validation_ok = False
            validation_output = f"Goal validation failed: {str(e)[:300]}"
        finally:
            if candidate_path and os.path.exists(candidate_path):
                try:
                    os.unlink(candidate_path)
                except OSError:
                    pass

        result["validation_passed"] = validation_ok
        result["validation_output"] = validation_output[:500]
        result["auto_fixes"] = auto_fixes
        if validation_ok:
            result["written"] = full_path
            result["message"] = "Goal validated and published successfully."
        else:
            result["error"] = "Validation failed. Fix reported issues and call dashi_fill_slide again with finalize=True."
    else:
        result["message"] = (
            f"Slide {slide_index} props saved. "
            f"{result['slides_remaining']} slides remaining. "
            f"Call with finalize=True when all slides are filled."
        )

    return result


async def dashi_scaffold(
    title: str,
    goal: str,
    theme: str,
    pages: int,
    layouts: list[str],
    out: str = None,
) -> dict:
    """Generate PPT scaffold with complete layout specs in one step.
    
    The Agent first chooses layouts through the read-only layout-query step, then
    passes those exact IDs here.  This tool internally inspects every selected
    layout and produces the type-safe skeleton; inspect-layout is never exposed
    as a separate generation step.
    
    Args:
        title: Presentation title
        goal: Presentation goal description (~30 chars)
        theme: Theme pack (e.g., "theme05", "theme07")
        pages: Number of slides
        out: Output path (defaults to output/<title>/goal.json)
        layouts: Required list of selected layout IDs, e.g. ["theme07_page001", "theme07_page043"]
    
    Returns:
        {
            "scaffold_path": "output/deck/goal.json",
            "fill_plan_path": null,
            "slides_spec": [
                {
                    "slide": 1,
                    "layout": "theme05_page001",
                    "label": "封面",
                    "text_props": {"titleL1": "string:42", "eyebrow": "string:18", ...},
                    "arrays": {
                        "metrics": {"cnt": 4, "vis": 3, "max": 4, "countKey": "metricCount"},
                        ...
                    },
                    "fill_plan": {text: [...], arrays: [...], media: [...]}
                },
                ...
            ],
            "goal_json": {完整的骨架 JSON},
            "mode": "selected"
        }
    
    Usage: dashi_scaffold(
        title="LLM发展史",
        goal="介绍大语言模型演进",
        theme="theme05",
        pages=6,
        layouts=["theme05_page004", "theme05_page017", "..."],
    )
    """
    if not layouts:
        return {
            "error": (
                "layouts is required. Query candidates with "
                'dashi_script(script_name="layout-query.mjs", ...), select one '
                "cover and unique body layouts, then call dashi_scaffold again."
            )
        }
    return await asyncio.to_thread(
        _dashi_scaffold_selected, title, goal, theme, pages, layouts, out
    )


def _dashi_scaffold_selected(title: str, goal: str, theme: str, pages: int,
                             layouts: list, out: str = None) -> dict:
    """Inspect selected layout-query results and build a type-safe skeleton.
    
    Flow:
    1. Build goal.json skeleton with selected layouts
    2. Call inspect-layout.mjs for each layout to get prop contracts
    3. Parse inspect output -> build slides_spec
    4. Return slides_spec + goal_json
    """
    import re as re_module
    import math
    
    root, proj = _dashi_paths()
    os.environ["DASHI_PPT_PROJECT_ROOT"] = proj
    os.environ["DASHI_PPT_SKILL_ROOT"] = root
    os.environ["DASHI_PPT_THEME_RUNTIME"] = os.environ.get("DASHI_PPT_THEME_RUNTIME", "prebuilt")
    
    node = shutil.which("node") or "node"
    subprocess_env = _project_subprocess_env(proj)

    for field_name, value in (("title", title), ("goal", goal), ("theme", theme)):
        if not isinstance(value, str) or not value.strip():
            return {"error": f"{field_name} must be a non-empty string"}
    if not isinstance(layouts, list) or not layouts:
        return {"error": "layouts must be a non-empty list"}
    if not isinstance(pages, int) or isinstance(pages, bool) or pages != len(layouts):
        return {"error": f"pages ({pages}) must equal len(layouts) ({len(layouts)})"}
    invalid_layout_names = [layout for layout in layouts if not isinstance(layout, str) or not layout.strip()]
    if invalid_layout_names:
        return {"error": "every layout must be a non-empty string"}
    duplicate_layouts = sorted({layout for layout in layouts if layouts.count(layout) > 1})
    if duplicate_layouts:
        return {"error": f"layouts must be unique; duplicates: {', '.join(duplicate_layouts)}"}
    wrong_theme = [layout for layout in layouts if not layout.startswith(f"{theme}_")]
    if wrong_theme:
        return {"error": f"layouts must belong to {theme}: {', '.join(wrong_theme)}"}
    
    # Default output path
    if not out:
        safe_title = re_module.sub(r'[^\w\-]', '_', title)[:30]
        out = f"output/{safe_title}/goal.json"
    try:
        out_rel, full_out = _resolve_project_output_path(proj, out, suffix=".json")
    except ValueError as exc:
        return {"error": f"Invalid out path: {exc}"}

    # Step 1: Build skeleton goal.json with selected layouts (props pre-filled by type)
    skeleton = {
        "title": title,
        "goal": goal,
        "themePack": theme,
        "pageCount": pages,
        "slides": [{"layout": layout, "props": {}} for layout in layouts],
    }

    # Step 2: Call inspect-layout.mjs for each layout (batch up to 8)
    slides_spec = []
    inspect_script = os.path.join(proj, "scripts", "inspect-layout.mjs")

    if not os.path.exists(inspect_script):
        return {"error": f"inspect-layout.mjs not found: {inspect_script}"}

    # Batch layouts (max 8 per call)
    for i in range(0, len(layouts), 8):
        batch = layouts[i:i+8]
        cmd = [node, inspect_script]
        for layout in batch:
            cmd.extend(["--layout", layout])

        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                timeout=120, cwd=proj, env=subprocess_env,
            )
            stdout = r.stdout or ""
            if r.returncode != 0:
                return {"error": f"inspect-layout failed: {r.stderr or stdout}"}

            # Parse the full JS response; inspect-layout normally wraps records in
            # {"layouts": [...]}, while older versions returned a bare list.
            try:
                inspect_results = _inspect_layout_items(stdout) if stdout.strip() else []
            except _json.JSONDecodeError:
                # Fallback to filtered
                filtered = _filter_inspect_output(stdout)
                try:
                    inspect_results = _inspect_layout_items(filtered) if filtered.strip() else []
                except _json.JSONDecodeError:
                    inspect_results = []

            # Match layouts to inspect results
            for j, layout in enumerate(batch):
                slide_num = i + j + 1

                # Find matching inspect result
                matched = None
                for ir in inspect_results:
                    if ir.get("layout") == layout:
                        matched = ir
                        break

                if matched:
                    allowed_keys = matched.get("allowedPublicPropKeys") or matched.get("allowedPropKeys", [])
                    raw_fill_plan = matched.get("fillPlan") or {"text": [], "arrays": [], "media": []}
                    fill_plan = _with_recommended_text_budgets(raw_fill_plan)

                    # Generate typed skeleton for this slide's props
                    typed_props = _build_prop_skeleton(matched)
                    # Update skeleton in-place: this guarantees types are correct
                    # in the on-disk goal.json so dashi_write_goal only patches values.
                    if i + j < len(skeleton["slides"]):
                        skeleton["slides"][i + j]["props"] = typed_props

                    # Compatibility summaries are derived from the canonical raw
                    # fillPlan. Keep fill_plan itself unchanged for nested/tuple types.
                    text_props = {}
                    for text_plan in fill_plan.get("text") or []:
                        key = text_plan.get("key", "")
                        type_info = text_plan.get("type", "string")
                        max_chars = text_plan.get("recommendedMaxChars") or text_plan.get("maxChars")
                        if max_chars:
                            text_props[key] = f"{type_info}:{max_chars}"
                        else:
                            text_props[key] = type_info

                    arrays = {}
                    for array_plan in fill_plan.get("arrays") or []:
                        key = array_plan.get("key", "")
                        visible = array_plan.get("visibleCount")
                        maximum = array_plan.get("maxCount")
                        arrays[key] = {
                            "cnt": visible,
                            "vis": visible,
                            "max": maximum,
                            **{name: value for name, value in array_plan.items()
                               if name not in {"key", "visibleCount", "maxCount"}},
                        }

                    slides_spec.append({
                        "slide": slide_num,
                        "layout": layout,
                        "label": matched.get("label", ""),
                        "text_props": text_props,
                        "arrays": arrays,
                        "allowed_keys": allowed_keys,
                        "fill_plan": fill_plan,
                    })
                else:
                    return {"error": f"inspect-layout returned no contract for layout: {layout}"}
        except Exception as e:
            return {"error": f"inspect-layout failed: {str(e)}"}

    # Publish the fully inspected, typed skeleton in one atomic replacement.
    _atomic_write_json(full_out, skeleton)
    
    return {
        "scaffold_path": out_rel,
        "fill_plan_path": None,
        "themePack": theme,
        "pageCount": pages,
        "slideCount": len(layouts),
        "slides_spec": slides_spec,
        "goal_json": skeleton,
        "mode": "selected",
        "stdout": "",
        "stderr": "",
        "returncode": 0,
    }
