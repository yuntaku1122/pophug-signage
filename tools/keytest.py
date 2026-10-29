"""押しボタン（テンキー改造品など）が、押した瞬間と離した瞬間を別々に送ってくるか、
何キーとして届くかを確認するツール（Mac上で実行）。
  使い方: venv/bin/python3 tools/keytest.py   → 開いた窓を選択して、ボタンを押す／離す／長押しする
  終了: ESC"""
import time
import pygame

pygame.init()
screen = pygame.display.set_mode((700, 300))
pygame.display.set_caption("pophug keytest")
font = pygame.font.SysFont("hiraginosans,applegothic,arial", 22)
log, downs = [], {}
running = True
while running:
    for e in pygame.event.get():
        if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
            running = False
        elif e.type == pygame.KEYDOWN:
            downs[e.key] = time.time()
            log.append(f"DOWN  {pygame.key.name(e.key)} (code {e.key})")
        elif e.type == pygame.KEYUP:
            held = time.time() - downs.pop(e.key, time.time())
            log.append(f"UP    {pygame.key.name(e.key)} (code {e.key})  押していた時間 {held:.2f}秒")
        print(log[-1]) if e.type in (pygame.KEYDOWN, pygame.KEYUP) else None
    screen.fill((20, 20, 30))
    for i, line in enumerate(log[-9:]):
        screen.blit(font.render(line, True, (230, 230, 230)), (20, 20 + i * 30))
    pygame.display.flip()
    pygame.time.wait(10)
pygame.quit()
