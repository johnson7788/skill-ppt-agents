/**
 * A2UI Smart Wrapper：循证卡专属自定义组件。
 *
 * basic catalog 没有 Badge/渐变头/彩色高亮，这里用 createComponentImplementation
 * 注册 3 个自定义组件，与全部 basic 组件合并成一个 evidenceCatalog（新 catalogId）。
 * 后端 mapper 的 createSurface 用同一个 catalogId、并 emit 这些组件类型。
 * 只做 basic 表达不了的三处（plan §6：最少 Smart Wrapper），其余仍走 basic + CSS。
 */
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

const basicComponents: ReactComponentImplementation[] = [
  Text, Image, Icon, Video, AudioPlayer, Row, Column, List, Card, Tabs,
  Divider, Modal, Button, TextField, CheckBox, ChoicePicker, Slider, DateTimeInput,
];

export const evidenceCatalog = new Catalog<ReactComponentImplementation>(
  EVIDENCE_CATALOG_ID,
  [...basicComponents, EvidenceHeader, EvidenceBadge, CautionBox],
  BASIC_FUNCTIONS,
);
