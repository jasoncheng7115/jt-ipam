// @novnc/novnc 沒附型別宣告；只宣告我們用到的 RFB 介面。
declare module "@novnc/novnc" {
  export default class RFB extends EventTarget {
    constructor(
      target: HTMLElement | null,
      url: string,
      options?: { credentials?: { username?: string; password?: string; target?: string } },
    );
    scaleViewport: boolean;
    background: string;
    disconnect(): void;
  }
}
