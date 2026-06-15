import { ref } from "vue";

// 子網路側邊選單刷新訊號：新增 / 編輯 / 刪除子網路後 bump()，
// MainLayout watch 此版本號 → 重新載入左選單的子網路樹（含繼承的單位分組）。
const version = ref(0);

export function useSubnetTree() {
  return {
    version,
    bump: () => { version.value += 1; },
  };
}
