import { useDialog } from "naive-ui";
import { useI18n } from "vue-i18n";
import { setRackPositions } from "@/api/racks";

/**
 * 換底圖時，機櫃原本的擺放座標要保留還是清掉：讓使用者明確選（交接包 physical-v1.2 第 10 節）。
 * 新圖的比例、方向可能完全不同，自動保留會把機櫃擺到錯的位置；自動清掉又會丟掉別人排好的配置。
 * 機房編輯視窗與平面圖元件兩個上傳入口共用。
 */
export function useFloorplanReplace() {
  const dialog = useDialog();
  const { t } = useI18n();
  /** 回傳 "keep" / "clear"，取消回 null（不上傳） */
  function ask(): Promise<"keep" | "clear" | null> {
    return new Promise((resolve) => {
      dialog.warning({
        title: t("floorplan.replace_title"), content: t("floorplan.replace_body"),
        positiveText: t("floorplan.replace_keep"), negativeText: t("floorplan.replace_clear"),
        closable: true, maskClosable: false,
        onPositiveClick: () => resolve("keep"), onNegativeClick: () => resolve("clear"),
        onClose: () => resolve(null),
      });
    });
  }
  async function clearPositions(locationId: string) {
    await setRackPositions(locationId, []);
  }
  return { ask, clearPositions };
}
