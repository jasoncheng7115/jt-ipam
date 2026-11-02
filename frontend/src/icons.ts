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
  FingerprintScan,
  Barcode,
  Industry,
  Copy,
  Archive,
  Undo,
  EditPencil,
  Trash,
  Refresh,
  RefreshDouble,
  ArrowUpCircle,
  Percentage,
  SortDown,
  SortUp,
  Search,
  Xmark,
  LinkSlash,
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
  Windows,
  DataTransferBoth,
  DatabaseExport,
  DatabaseScript,
  Text,
  ServerConnection,
  Settings,
  GraphUp,
  ScaleFrameEnlarge,
  ModernTv,
  HdDisplay,
  FrameSimple,
  SendDiagonal,
  Hammer,
  Terminal,
  Expand,
  Reduce,
  NavArrowDown,
  NavArrowRight,
  OpenNewWindow,
  DoubleCheck,
  // Admin / 安全
  ShieldCheck,
  Shield,
  ShieldAlert,
  Lock,
  Prohibition,
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
  Computer,
  SunLight,
  Database,
  Send,
  Activity,
  Antenna,
  AntennaSignal,
  BrainResearch,
  Flash,
  DeliveryTruck,
  Wrench,
  Hourglass,
  TimerOff,
  BellOff,
  NavArrowUp,
  Www,
  // Status
  CheckCircle,
  XmarkCircle,
  ClockRotateRight,
  Timer,
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
  Upload,
  FolderPlus,
  Filter,
  ArrowRight,
  ArrowUp,
  HelpCircle,
  PasteClipboard,
  Menu,
  ClipboardCheck,
  Play,
  Sparks,
  PlugTypeA,
  Cube,
  NetworkReverse,
  ShareAndroid,
  NetworkRight,
  Rings,
  WifiOff,
  UserXmark,
  DatabaseBackup,
} from "@iconoir/vue";

// ── 通用 ──
export const PlusIcon = Plus;
/** 手機版左上角：打開側欄選單 */
export const MenuIcon = Menu;
/** 拖拉把手（欄位選單調整欄位順序）：三條橫線是常見的「抓這裡拖」圖示 */
export const DragHandleIcon = Menu;
export const CloneIcon = Copy;
export const ArchiveIcon = Archive;
export const RestoreIcon = Undo;
export const EditIcon = EditPencil;
export const DeleteIcon = Trash;
export const RefreshIcon = Refresh;
export const SyncIcon = RefreshDouble;
export const SearchIcon = Search;
export const CancelIcon = Xmark;
export const DisconnectedIcon = LinkSlash;   // 主控台斷線覆蓋層
export const SaveIcon = Check;
export const CheckIcon = Check;
export const WarnIcon = WarningTriangle;
export const UpgradeIcon = ArrowUpCircle;
export const InfoIcon = InfoCircle;
export const EyeIcon = Eye;
export const EyeOffIcon = EyeClosed;
// 「忽略」：判斷為誤報而收起來，不是刪除 —— 用閉眼比用叉叉貼切
export const DismissIcon = EyeClosed;

// ── 狀態 ──
export const OkIcon = CheckCircle;
export const FailIcon = XmarkCircle;
export const PendingIcon = ClockRotateRight;
/** 主控台狀態列的連線時間 */
export const ElapsedIcon = Timer;
export const TasksIcon = ClockRotateRight;
export const MissingIcon = WarningTriangle;
export const BellIcon = Bell;

// ── Subnet detail 子卡片 ──
export const UsageIcon = StatsReport;
export const GridIcon = GridPlus;
export const ListIcon = List;
export const SelectAllIcon = DoubleCheck;

// ── Customers / 管理單位 ──
export const CustomersIcon = Group;  // 借用 Group icon，視覺上「一群人」

// ── 導覽 (sidebar menu)──
export const DashboardIcon = Home;
export const SectionsIcon = Folder;
export const SubnetsIcon = Network;
export const AddressesIcon = IpAddressTag;
// 排序控制項用：排序欄位（IP／主機名稱／可用率）與方向（遞增／遞減）
export const HostnameIcon = Text;
export const SlaIcon = Percentage;
export const SortAscIcon = SortUp;
export const SortDescIcon = SortDown;
export const IPChangesIcon = ClockRotateRight;
export const VlansIcon = Internet;
export const VrfsIcon = Link;
export const LinkIcon = Link;
export const NatIcon = RefreshDouble;
export const DevicesIcon = Server;
export const RacksIcon = ServerConnection;
// IP 角色標記（清單視覺化，緊湊 icon）：閘道 / DHCP 伺服器
export const GatewayIcon = Internet;
export const DhcpServerIcon = Server;
// 「整合 Windows DHCP」選單用。不共用 DhcpServerIcon —— 那個是 IP 清單上的「DHCP 伺服器
// 角色」標記，語意不同；而且 Server 這顆與 Proxmox／VMware 長得一樣，三個選單分不出來。
export const WindowsDhcpIcon = Windows;
// 獨立 Kea（jt-ipam 拉 API）／ISC DHCP（代理讀設定與租約檔）—— 兩個選單要分得出來，也不能跟 Server 撞
export const KeaDhcpIcon = DataTransferBoth;
export const IscDhcpIcon = DatabaseScript;
// ISOinsight 整合（登入設備的網頁介面讀 DHCP 租約）：跟 Kea 的雙向箭頭、ISC 的設定檔圖示分得出來
export const IsoInsightIcon = DatabaseExport;
// Technitium DHCP（同一台還是 DNS 伺服器）：跟 Kea／ISC／ISOinsight 的圖示分得出來
export const TechnitiumIcon = NetworkReverse;
// RustDesk Server（開源版）：選單與 IP 詳細資料的「以 RustDesk 連線」。不用 R 字螢幕 —— 那顆是 RDP
// RustDesk 網頁連線的工具列：「螢幕」選單（多螢幕切換）、「畫質」選單、螢幕選單裡的「解析度」子選單
export const ScreensIcon = ModernTv;
export const QualityIcon = HdDisplay;
export const ResolutionIcon = FrameSimple;
export const LocationsIcon = MapPin;
// DHCP 固定分配：這個位址被綁給某張網卡，不會被回收給別台
export const ReservedIcon = Lock;
// DHCP 排除：DHCP 伺服器把這個位址排除在發放範圍外（不會發給任何人，通常是設固定 IP 的設備）
export const DhcpExcludedIcon = Prohibition;
export const PinIcon = Pin;
export const RequestsIcon = MultiplePages;
export const TopologyIcon = GraphUp;
// 連線診斷（ping / traceroute / port…）：實際送封包的那一類工具。
// 不能用 Antenna —— 那是「無線連線」與「掃描代理」在用的，選單上會撞在一起。
// 用脈搏線：這一類工具量的就是「通不通、多快」。
export const NetDiagIcon = Activity;
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
/** 主題「自動」：跟著作業系統 */
export const ThemeAutoIcon = Computer;
export const GroupsIcon = Group;
export const CustomFieldsIcon = Page;
export const AnomalyIcon = ShieldAlert;
// AI 巡檢：跟異常偵測分開的圖示 —— 一個是量到的事實、一個是模型的推測，
// 選單上並排時要一眼分得出來
export const AiAuditIcon = BrainResearch;
/** IP 變更評估：檢核板＝評估（只讀，不會真的改） */
export const ChangeImpactIcon = ClipboardCheck;
export const ReviewIcon = ShieldCheck;      // 覆核
export const PlayIcon = Play;               // 開始（維護）
export const AiIcon = Sparks;               // 請 AI 草擬
/** 管理選單的「外部系統整合」子樹 */
export const IntegrationsIcon = PlugTypeA;
/** IP 變更評估 M2 的服務 */
export const ServiceIcon = Cube;
export const DnsIcon = Globe;
export const LibreNMSIcon = Cloud;
export const FirewallIcon = Shield;
export const WazuhIcon = ShieldAlert;
export const ScanAgentsIcon = Antenna;
export const SnmpIcon = AntennaSignal;        // SNMP 收集（掃描代理讀網路設備）
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
/** 租用設備（向供應商租用、要寄回歸還） */
export const LeaseIcon = DeliveryTruck;
/** 維護排程（維護期間不告警） */
export const MaintenanceIcon = Wrench;
/** 被靜音的告警 */
export const MuteIcon = BellOff;
/** 延長維護 */
export const ExtendIcon = Hourglass;
/** 提前結束維護 */
export const EndNowIcon = TimerOff;
/** 網域（註冊商、到期日、NS） */
export const DomainIcon = Www;
/** 對外路徑（網際網路 → WAN → 防火牆 → 內部主機） */
export const PathIcon = DataTransferBoth;
export const ArrowUpIcon = NavArrowUp;
export const ArrowDownIcon = NavArrowDown;
export const LockIcon = Lock;
export const VpnIcon = Globe;

// ── 認證 / 操作 ──
export const LoginIcon = LogIn;
export const LogoutIcon = LogOut;
export const TokenIcon = Key;
export const TestIcon = CheckCircle;
// IP 詳細頁的「探測」：辨識這個位址是什麼主機
export const IdentifyIcon = FingerprintScan;
/** MAC 位址（網卡的硬體識別碼）：選單的「MAC 位址」 */
export const MacIcon = Barcode;
/** 網卡製造商（依 MAC 前綴查到的廠牌） */
export const VendorIcon = Industry;

/**
 * 把 Iconoir icon 包成 NMenu / NDropdown / NTabs 認得的 render function。
 * 用法：{ label: "Users", icon: renderIcon(UsersIcon) }
 */
export const ChatHistoryIcon = ChatBubbleQuestion;
export const ExportIcon = Download;
export const DownloadIcon = Download;
export const UploadIcon = Upload;
export const NewFolderIcon = FolderPlus;
export const FilterIcon = Filter;
export const MoveIcon = ArrowRight;   // 搬移到其他目錄
export const UpLevelIcon = ArrowUp;   // 回上一層目錄
export const UnregisteredIcon = HelpCircle;   // 自動收錄、未經登記的位址
export const PasteIcon = PasteClipboard;
export const CopyIcon = Copy;
export const TerminalIcon = Terminal;
// 螢幕外框 + 字母圖示：RDP=R / VNC=V，靠字母直接區分（比找近似 glyph 更直觀）。
function screenLetterIcon(letter: string) {
  // 細監視器外框 + 佔滿螢幕的大粗字母，讓 R/V/N 在小按鈕上也一眼可辨
  return () => h("svg", {
    xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 24 24",
    width: "1em", height: "1em", fill: "none",
  }, [
    h("rect", { x: 1.75, y: 3, width: 20.5, height: 15, rx: 2.4,
      stroke: "currentColor", "stroke-width": 1.4 }),
    h("path", { d: "M12 18v2.6", stroke: "currentColor", "stroke-width": 1.5 }),
    h("path", { d: "M8 20.6h8", stroke: "currentColor", "stroke-width": 1.5,
      "stroke-linecap": "round" }),
    h("text", {
      x: 12, y: 15.1, "text-anchor": "middle", "font-size": 13.5, "font-weight": 800,
      fill: "currentColor", stroke: "currentColor", "stroke-width": 0.3,
      "font-family": "system-ui, -apple-system, sans-serif",
    }, letter),
  ]);
}
export const DisplayIcon = screenLetterIcon("R");  // RDP
export const VncIcon = screenLetterIcon("V");      // VNC
export const NoVncIcon = screenLetterIcon("N");    // noVNC（PVE 圖形主控台）
// RustDesk：同一個螢幕外框，裡面兩個上下錯開的半圓（取 RustDesk 標誌的意象但不照抄；使用者 2026-10-06）。
// 以前用一般的電腦圖示，跟 RDP／VNC 的「螢幕＋符號」不同一家族
export const RustDeskIcon = () => h("svg", {
  xmlns: "http://www.w3.org/2000/svg", viewBox: "0 0 24 24",
  width: "1em", height: "1em", fill: "none",
}, [
  h("rect", { x: 1.75, y: 3, width: 20.5, height: 15, rx: 2.4,
    stroke: "currentColor", "stroke-width": 1.4 }),
  h("path", { d: "M12 18v2.6", stroke: "currentColor", "stroke-width": 1.5 }),
  h("path", { d: "M8 20.6h8", stroke: "currentColor", "stroke-width": 1.5, "stroke-linecap": "round" }),
  h("path", { d: "M10.6 6.3A3.3 3.3 0 0 0 10.6 12.9", stroke: "currentColor", "stroke-width": 2.3,
    "stroke-linecap": "round" }),
  h("path", { d: "M13.4 8.1A3.3 3.3 0 0 1 13.4 14.7", stroke: "currentColor", "stroke-width": 2.3,
    "stroke-linecap": "round" }),
]);
export const ExpandIcon = Expand;                  // 重新調整大小 / 自動縮放
export const ReduceIcon = Reduce;                  // 原始解析度（1:1）
export const KeyIcon = Key;                        // 送出按鍵
export const ChevronDownIcon = NavArrowDown;
export const ChevronRightIcon = NavArrowRight;
export const OpenNewWindowIcon = OpenNewWindow;

export function renderIcon(Icon: any, size = 18) {
  return () => h(NIcon, { size }, () => h(Icon));
}
/** SFTP 檔案傳輸（沿用資料夾圖示 —— 這個功能就是在瀏覽遠端目錄） */
export const FilesIcon = Folder;

// IP 變更評估關係圖的排列方式（使用者 2026-10-08：按鈕要有圖示）
export const GraphForceIcon = ShareAndroid;   // 自動排列：幾個點互相連著
export const GraphRadialIcon = SunLight;      // 放射：中心向外
export const GraphTreeIcon = NetworkRight;    // 樹狀：由左往右
export const GraphRingsIcon = Rings;          // 依影響分層：同心圈
// 戰情室（10-22 加圖示）：失聯、未授權、備份
export const OfflineIcon = WifiOff;
export const UnauthorizedIcon = UserXmark;
export const BackupIcon = DatabaseBackup;
