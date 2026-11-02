from pathlib import Path

from PIL import Image

SHEET_PATH = Path(__file__).resolve().parents[2] / "assets" / "chibi" / "sprites.png"
FRAMES_PER_STATE = 8
ESTADO_ROW = {"idle": 0, "pensando": 1, "digitando": 2, "feliz": 3, "processando": 4, "notificacoes": 5}
GRID_LEFT, GRID_TOP, CELL_W, CELL_H = 0, 0, 181, 181  # cartela gerada em assets/chibi: 6 linhas x 8 colunas, células 181x181, sem gutters

# Tk (Windows) não compõe o canal alfa do PNG contra o desktop: ele pinta o
# alfa sobre a cor de fundo opaca da janela/Label. Para o fundo ficar
# realmente transparente usamos "color-key": uma cor improvável no sprite
# (magenta puro) que a janela declara via wm_attributes("-transparentcolor",
# ...) como um buraco literal. Pixels de baixo alfa (fundo/antialiasing)
# viram essa cor-chave opaca; pixels de alfa alto (personagem) ficam
# totalmente opacos sem resíduo de alfa parcial — isso evita a franja
# colorida que bordas antialiased deixariam se só trocássemos a cor sem
# também "fechar" o alfa.
KEY_COLOR = (255, 0, 255)  # magenta puro — ausente do sprite real (verificado)
KEY_COLOR_HEX = "#%02x%02x%02x" % KEY_COLOR
ALPHA_THRESHOLD = 128


def _apply_color_key(frame: "Image.Image", key=KEY_COLOR, threshold=ALPHA_THRESHOLD) -> "Image.Image":
    frame = frame.convert("RGBA")
    pixels = frame.load()
    w, h = frame.size
    key_pixel = key + (255,)
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            pixels[x, y] = key_pixel if a < threshold else (r, g, b, 255)
    return frame


def _largest_component_bbox(mask: "Image.Image"):
    """Bounding box do maior componente conectado de pixels opacos na máscara.

    Algumas cartelas desenham decorações por estado (balão de pensamento,
    engrenagem, exclamação) na mesma camada de alfa do personagem, soltas
    dele. Um bbox sobre *todos* os pixels opacos inclui essas decorações,
    que mudam de posição/tamanho frame a frame — o personagem "pula"
    dentro da célula acompanhando a decoração, não por imprecisão da arte
    do próprio personagem. Restringir ao maior componente conectado (o
    corpo do personagem, sempre a maior massa de pixels) ignora isso.
    """
    w, h = mask.size
    pixels = mask.load()
    seen = bytearray(w * h)
    best_bbox = None
    best_size = 0
    for sy in range(h):
        for sx in range(w):
            idx = sy * w + sx
            if seen[idx] or not pixels[sx, sy]:
                continue
            stack = [(sx, sy)]
            seen[idx] = 1
            size = 0
            minx = maxx = sx
            miny = maxy = sy
            while stack:
                cx, cy = stack.pop()
                size += 1
                if cx < minx:
                    minx = cx
                elif cx > maxx:
                    maxx = cx
                if cy < miny:
                    miny = cy
                elif cy > maxy:
                    maxy = cy
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        nidx = ny * w + nx
                        if not seen[nidx] and pixels[nx, ny]:
                            seen[nidx] = 1
                            stack.append((nx, ny))
            if size > best_size:
                best_size = size
                best_bbox = (minx, miny, maxx, maxy)
    return best_bbox


def _recenter_frame(frame: "Image.Image", canvas_w: int, canvas_h: int, threshold=ALPHA_THRESHOLD) -> "Image.Image":
    """Realinha o conteúdo opaco do frame ao centro da célula.

    A cartela original não alinha o personagem ao mesmo ponto em cada
    frame de uma animação (medido: até ~50px de variação no centro
    horizontal entre frames do mesmo estado) — sem isso, o bichinho
    parece "andar" dentro da janela fixa só por imprecisão da arte, não
    por um ciclo de passos desenhado de propósito.

    A bbox usa o mesmo limiar de _apply_color_key (não qualquer alfa > 0):
    franjas de antialiasing quase invisíveis nas bordas, se contassem,
    tornariam o próprio cálculo do deslocamento ruidoso entre frames.
    Depois, a colagem é feita sem máscara — com máscara, paste usa o
    próprio alfa da fonte como peso de mistura e teria "ao quadrado" o
    alfa parcial restante (ex.: 40 -> ~6), apagando antialiasing real.
    """
    alpha = frame.getchannel("A")
    mask = alpha.point(lambda a: 255 if a >= threshold else 0)
    bbox = _largest_component_bbox(mask)
    if bbox is None:
        return frame
    x0, y0, x1, y1 = bbox
    dx = round(canvas_w / 2 - (x0 + x1) / 2)
    dy = round(canvas_h / 2 - (y0 + y1) / 2)
    if dx == 0 and dy == 0:
        return frame
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    canvas.paste(frame, (dx, dy))
    return canvas


def slice_sheet(path, grid_left: int, grid_top: int, cell_w: int, cell_h: int) -> dict:
    sheet = Image.open(path).convert("RGBA")
    out = {}
    for estado, row in ESTADO_ROW.items():
        frames = []
        for col in range(FRAMES_PER_STATE):
            x = grid_left + col * cell_w
            y = grid_top + row * cell_h
            crop = sheet.crop((x, y, x + cell_w, y + cell_h))
            frames.append(_apply_color_key(_recenter_frame(crop, cell_w, cell_h)))
        out[estado] = frames
    return out


def load_frames() -> dict:
    return slice_sheet(SHEET_PATH, GRID_LEFT, GRID_TOP, CELL_W, CELL_H)
