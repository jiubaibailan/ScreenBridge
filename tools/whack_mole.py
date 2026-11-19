"""文字版打地鼠，用于测试 ScreenBridge 的实时读屏和点击能力。"""

import random
import tkinter as tk
from tkinter import ttk


class WhackMole:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ScreenBridge · 文字打地鼠")
        self.root.geometry("520x500")
        self.root.resizable(False, False)
        self.score = 0
        self.round_no = 0
        self.mole = None
        self.running = True

        title = ttk.Label(root, text="文字版打地鼠", font=("Segoe UI", 20, "bold"))
        title.pack(pady=(16, 4))
        self.status = ttk.Label(root, text="读取网格，点击出现 🐹 的洞位", font=("Segoe UI", 12))
        self.status.pack(pady=(0, 8))
        self.score_label = ttk.Label(root, text="得分：0    回合：0", font=("Segoe UI", 12, "bold"))
        self.score_label.pack(pady=(0, 12))

        board = ttk.Frame(root)
        board.pack()
        self.buttons: list[ttk.Button] = []
        for index in range(16):
            button = ttk.Button(board, text=f"洞位 {index + 1}\n[ 空 ]", width=14,
                                command=lambda i=index: self.whack(i))
            button.grid(row=index // 4, column=index % 4, padx=5, pady=5, ipady=10)
            self.buttons.append(button)

        ttk.Label(root, text="每秒刷新一次。请在鼹鼠消失前点击正确洞位。", foreground="#555").pack(pady=14)
        self.next_round()

    def next_round(self) -> None:
        if not self.running:
            return
        self.round_no += 1
        self.mole = random.randrange(16)
        for index, button in enumerate(self.buttons):
            text = f"洞位 {index + 1}\n[ 🐹 ]" if index == self.mole else f"洞位 {index + 1}\n[ 空 ]"
            button.configure(text=text)
        self.score_label.configure(text=f"得分：{self.score}    回合：{self.round_no}")

    def whack(self, index: int) -> None:
        if index == self.mole:
            self.score += 1
            self.status.configure(text="命中！继续寻找下一个鼹鼠。")
            self.next_round()
        else:
            self.status.configure(text="没打中，再观察当前网格。")
        self.score_label.configure(text=f"得分：{self.score}    回合：{self.round_no}")


def main() -> None:
    root = tk.Tk()
    WhackMole(root)
    root.mainloop()


if __name__ == "__main__":
    main()
