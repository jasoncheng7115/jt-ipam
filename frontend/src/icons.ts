/**
 * jt-ipam 統一 icon 出口 (Iconoir，MIT)。
 *
 * 規範：
 * - 不用 emoji；page title / button / modal title / menu / tool 都用此處 icon
 * - 一律 re-export 自 @iconoir/vue，命名語意化 (PlusIcon、EditIcon …)
 * - 若需新增，請在此檔加 alias，view 端只 import 這支
 *
 * 用法：
 *   import { PlusIcon, EditIcon } from "@/icons";
 *   <n-button><template #icon><n-icon><PlusIcon /></n-icon></template>新增</n-button>
 *
 *   或用 helper：
 *   import { renderIcon } from "@/icons";
 *   { icon: renderIcon(PlusIcon) }   // 給 NMenu / NDropdown 用
 */
import { h } from "vue";
import { NIcon } from "naive-ui";
import {
  // 通用動作
  Plus,
  Copy,
  Archive,
  Undo,
  EditPencil,
  Trash,
  Refresh,
  RefreshDouble,
  ArrowUpCircle,
  Search,
  Xmark,
  Check,
  WarningTriangle,
  InfoCircle,
  Eye,
  EyeClosed,
  // 應用 / 導覽
  Home,
  Page,
  Reports,
  Folder,
  Network,
  Internet,
  IpAddressTag,
  Server,
  ServerConnection,
  Settings,
  GraphUp,
  ScaleFrameEnlarge,
  SendDiagonal,
  Hammer,
  // Admin / 安全
  ShieldCheck,
  Shield,
  ShieldAlert,
  Lock,
  LogIn,
  LogOut,
  User,
  Group,
  Key,
  // 整合
  Link,
  Puzzle,
  Cloud,
  Globe,
  Language,
  HalfMoon,
  SunLight,
  Database,
  Send,
  Antenna,
  Flash,
  // Status
  CheckCircle,
  XmarkCircle,
  ClockRotateRight,
  Pin,
  MapPin,
  MultiplePages,
  Bell,
  // Subnet detail
  StatsReport,
  GridPlus,
  List,
  ChatBubbleQuestion,
  Download,
} from "@iconoir/vue";

// ── 通用 ──
export const PlusIcon = Plus;
export const CloneIcon = Copy;
export const ArchiveIcon = Archive;
export const RestoreIcon = Undo;
export const EditIcon = EditPencil;
export const DeleteIcon = Trash;
export const RefreshIcon = Refresh;
export const SyncIcon = RefreshDouble;
export const SearchIcon = Search;
export const CancelIcon = Xmark;
export const SaveIcon = Check;
export const CheckIcon = Check;
export const WarnIcon = WarningTriangle;
export const UpgradeIcon = ArrowUpCircle;
export const InfoIcon = InfoCircle;
export const EyeIcon = Eye;
export const EyeOffIcon = EyeClosed;

// ── 狀態 ──
export const OkIcon = CheckCircle;
export const FailIcon = XmarkCircle;
export const PendingIcon = ClockRotateRight;
export const TasksIcon = ClockRotateRight;
export const MissingIcon = WarningTriangle;
export const BellIcon = Bell;

// ── Subnet detail 子卡片 ──
export const UsageIcon = StatsReport;
export const GridIcon = GridPlus;
export const ListIcon = List;

// ── Customers / 管理單位 ──
export const CustomersIcon = Group;  // 借用 Group icon，視覺上「一群人」

// ── 導覽 (sidebar menu)──
export const DashboardIcon = Home;
export const SectionsIcon = Folder;
export const SubnetsIcon = Network;
export const AddressesIcon = IpAddressTag;
export const IPChangesIcon = ClockRotateRight;
export const VlansIcon = Internet;
export const VrfsIcon = Link;
export const LinkIcon = Link;
export const NatIcon = RefreshDouble;
export const DevicesIcon = Server;
export const RacksIcon = ServerConnection;
export const LocationsIcon = MapPin;
export const PinIcon = Pin;
export const RequestsIcon = MultiplePages;
export const TopologyIcon = GraphUp;
export const FitIcon = ScaleFrameEnlarge;
export const SendIcon = SendDiagonal;
export const ToolsIcon = Hammer;
export const SettingsIcon = Settings;

// ── 管理 ──
export const AdminIcon = ShieldCheck;
export const AuditIcon = Reports;
export const UsersIcon = User;
export const AccountIcon = User;
export const LanguageIcon = Language;
export const ThemeDarkIcon = HalfMoon;
export const ThemeLightIcon = SunLight;
export const GroupsIcon = Group;
export const CustomFieldsIcon = Page;
export const AnomalyIcon = ShieldAlert;
export const DnsIcon = Globe;
export const LibreNMSIcon = Cloud;
export const FirewallIcon = Shield;
export const WazuhIcon = ShieldAlert;
export const ScanAgentsIcon = Antenna;
export const WebhooksIcon = Send;
export const MigrationIcon = Database;
export const ImportIcon = Database;
export const PluginsIcon = Puzzle;

// ── Phase 3 進階 ──
export const Phase3Icon = Server;
export const AdvancedIcon = Group;
export const VirtualizationIcon = Server;
export const PhysicalIcon = ServerConnection;
export const PowerIcon = Flash;
export const LockIcon = Lock;
export const VpnIcon = Globe;

// ── 認證 / 操作 ──
export const LoginIcon = LogIn;
export const LogoutIcon = LogOut;
export const TokenIcon = Key;
export const TestIcon = CheckCircle;

/**
 * 把 Iconoir icon 包成 NMenu / NDropdown / NTabs 認得的 render function。
 * 用法：{ label: "Users", icon: renderIcon(UsersIcon) }
 */
export const ChatHistoryIcon = ChatBubbleQuestion;
export const ExportIcon = Download;
export const CopyIcon = Copy;

export function renderIcon(Icon: any, size = 18) {
  return () => h(NIcon, { size }, () => h(Icon));
}
