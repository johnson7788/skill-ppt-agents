/**
 * A2UI Smart Wrapper：循证卡专属自定义组件。
 *
 * basic catalog 没有 Badge/渐变头/彩色高亮，这里用 createComponentImplementation
 * 注册 3 个自定义组件，与全部 basic 组件合并成一个 evidenceCatalog（新 catalogId）。
 * 后端 mapper 的 createSurface 用同一个 catalogId、并 emit 这些组件类型。
 * 只做 basic 表达不了的三处（plan §6：最少 Smart Wrapper），其余仍走 basic + CSS。
 */
import {useState} from 'react';
import {z} from 'zod';
import {
  createComponentImplementation,
  Text,
  Image,
  Icon,
  Video,
  AudioPlayer,
  Row,
  Column,
  List,
  Card,
  Tabs,
  Divider,
  Modal,
  Button,
  TextField,
  CheckBox,
  ChoicePicker,
  Slider,
  DateTimeInput,
  type ReactComponentImplementation,
} from '@a2ui/react/v0_9';
import {Catalog, BASIC_FUNCTIONS} from '@a2ui/web_core/v0_9';

export const EVIDENCE_CATALOG_ID = 'https://evidence-a2ui.local/catalog/v1';

const LEVEL_COLORS: Record<string, string> = {
  A: '#16a34a', // 绿
  B: '#2563eb', // 蓝
  C: '#d97706', // 橙
  D: '#6b7280', // 灰
};

// 绿色渐变标题条
const EvidenceHeader = createComponentImplementation(
  {name: 'EvidenceHeader', schema: z.object({title: z.string(), subtitle: z.string()}).strict()},
  ({props}) => (
    <div className="ev-header">
      <span className="ev-header-title">{props.title}</span>
      <span className="ev-header-sub">{props.subtitle}</span>
    </div>
  ),
);

// 证据等级彩色药丸徽章
const EvidenceBadge = createComponentImplementation(
  {name: 'EvidenceBadge', schema: z.object({level: z.string()}).strict()},
  ({props}) => (
    <span className="ev-badge" style={{background: LEVEL_COLORS[props.level] ?? LEVEL_COLORS.D}}>
      {props.level} 级证据
    </span>
  ),
);

// 红色强调的注意事项
const CautionBox = createComponentImplementation(
  {name: 'CautionBox', schema: z.object({highlight: z.string(), text: z.string()}).strict()},
  ({props}) => (
    <div className="ev-caution">
      {props.highlight && <span className="ev-caution-hl">{props.highlight}</span>}
      <span>{props.text}</span>
    </div>
  ),
);

// 自测问卷/量表：后端只给量表定义，打分在这里做（选项 score 求和 → 落 band）。
// round-trip 免除——组件自持状态、本地评分、就地出结果，不回传 agent。
type QuizBand = {min: number; max: number; label: string; advice: string};

// 纯函数：各题所选选项分值求和 → 命中的档位（未命中返回 null）。e2e 覆盖。
function scoreQuiz(picked: number[], scores: number[], bands: QuizBand[]) {
  const total = picked.reduce((sum, opt) => sum + (scores[opt] ?? 0), 0);
  const band = bands.find(b => total >= b.min && total <= b.max) ?? null;
  return {total, band};
}

const Questionnaire = createComponentImplementation(
  {
    name: 'Questionnaire',
    schema: z
      .object({
        title: z.string(),
        intro: z.string(),
        options: z.array(z.object({label: z.string(), score: z.number()})),
        items: z.array(z.string()),
        bands: z.array(
          z.object({min: z.number(), max: z.number(), label: z.string(), advice: z.string()}),
        ),
        disclaimer: z.string(),
      })
      .strict(),
  },
  ({props}) => {
    const {options, items, bands} = props;
    const [picked, setPicked] = useState<number[]>(() => items.map(() => -1));
    const [result, setResult] = useState<ReturnType<typeof scoreQuiz> | null>(null);
    const allAnswered = picked.every(p => p >= 0);
    const submit = () => setResult(scoreQuiz(picked, options.map(o => o.score), bands));
    return (
      <div className="quiz">
        <div className="quiz-title">{props.title}</div>
        {props.intro && <div className="quiz-intro">{props.intro}</div>}
        {items.map((q: string, qi: number) => (
          <div className="quiz-item" key={qi}>
            <div className="quiz-q">
              {qi + 1}. {q}
            </div>
            <div className="quiz-opts">
              {options.map((o, oi) => (
                <label className={`quiz-opt${picked[qi] === oi ? ' sel' : ''}`} key={oi}>
                  <input
                    type="radio"
                    name={`q${qi}`}
                    checked={picked[qi] === oi}
                    onChange={() => setPicked(p => p.map((v, i) => (i === qi ? oi : v)))}
                  />
                  {o.label}
                </label>
              ))}
            </div>
          </div>
        ))}
        <button className="quiz-submit" disabled={!allAnswered} onClick={submit}>
          {allAnswered ? '查看结果' : `请完成全部 ${items.length} 题`}
        </button>
        {result && (
          <div className="quiz-result">
            <div className="quiz-score">
              总分 {result.total}
              {result.band && <span className="quiz-band"> · {result.band.label}</span>}
            </div>
            {result.band && <div className="quiz-advice">{result.band.advice}</div>}
          </div>
        )}
        <div className="quiz-disclaimer">{props.disclaimer}</div>
      </div>
    );
  },
);

const basicComponents: ReactComponentImplementation[] = [
  Text, Image, Icon, Video, AudioPlayer, Row, Column, List, Card, Tabs,
  Divider, Modal, Button, TextField, CheckBox, ChoicePicker, Slider, DateTimeInput,
];

export const evidenceCatalog = new Catalog<ReactComponentImplementation>(
  EVIDENCE_CATALOG_ID,
  [...basicComponents, EvidenceHeader, EvidenceBadge, CautionBox, Questionnaire],
  BASIC_FUNCTIONS,
);
