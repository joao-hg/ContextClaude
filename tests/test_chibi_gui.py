import os
import tempfile
import unittest

from PIL import Image

from contextclaude import chibi_gui


class TestChibiGui(unittest.TestCase):
    def test_slice_sheet_returns_six_states_of_eight_frames_each(self):
        sheet = Image.new("RGBA", (8 * 10, 6 * 10))
        for row in range(6):
            for col in range(8):
                color = (row * 40, col * 30, 0, 255)
                for x in range(10):
                    for y in range(10):
                        sheet.putpixel((col * 10 + x, row * 10 + y), color)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sheet.png")
            sheet.save(path)
            frames = chibi_gui.slice_sheet(path, grid_left=0, grid_top=0, cell_w=10, cell_h=10)
        self.assertEqual(set(frames.keys()), set(chibi_gui.ESTADO_ROW.keys()))
        self.assertEqual(len(frames["pensando"]), chibi_gui.FRAMES_PER_STATE)
        self.assertEqual(frames["digitando"][3].getpixel((0, 0)), (2 * 40, 3 * 30, 0, 255))

    def test_low_alpha_pixels_become_opaque_key_color_no_fringe(self):
        """Pixels com alfa baixo (fundo, borda antialiased) devem virar a cor-chave
        totalmente opaca -- sem isso, o Label/janela do Tk pinta o alfa parcial
        sobre o fundo opaco em vez de deixar o desktop aparecer atrás."""
        sheet = Image.new("RGBA", (10, 10), (10, 20, 30, 40))  # alfa baixo: "fundo"
        for x in range(10):
            for y in range(10):
                sheet.putpixel((x, y), (200, 150, 50, 200))  # alfa alto: "personagem"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sheet.png")
            sheet.save(path)
            frames = chibi_gui.slice_sheet(path, grid_left=0, grid_top=0, cell_w=10, cell_h=10)
        px = frames["idle"][0].getpixel((0, 0))
        self.assertEqual(px, (200, 150, 50, 255))  # opaco, cor original preservada

    def test_low_alpha_pixel_flattens_to_key_color(self):
        sheet = Image.new("RGBA", (80, 10 * 6), (0, 0, 0, 0))
        for x in range(10):
            for y in range(10):
                sheet.putpixel((x, y), (10, 20, 30, 40))  # alfa < threshold
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sheet.png")
            sheet.save(path)
            frames = chibi_gui.slice_sheet(path, grid_left=0, grid_top=0, cell_w=10, cell_h=10)
        px = frames["idle"][0].getpixel((0, 0))
        self.assertEqual(px, chibi_gui.KEY_COLOR + (255,))

    def test_recenter_ignores_disconnected_decoration_blob(self):
        """Uma decoração desconectada do personagem (balão de pensamento,
        engrenagem, exclamação) não pode puxar o centro calculado para longe
        do personagem -- senão ele "pula" de frame a frame dentro do mesmo
        estado, conforme a decoração aparece/some/muda de tamanho."""
        cw, ch = 40, 40
        frame = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        char_box = (2, 15, 12, 25)  # personagem fora do centro, perto da borda esquerda
        for x in range(*char_box[::2]):
            for y in range(*char_box[1::2]):
                frame.putpixel((x, y), (200, 150, 50, 255))
        deco_box = (35, 2, 39, 6)  # decoração desconectada no canto oposto
        for x in range(*deco_box[::2]):
            for y in range(*deco_box[1::2]):
                frame.putpixel((x, y), (255, 255, 255, 255))

        out = chibi_gui._recenter_frame(frame, cw, ch)

        char_pixels = [
            (x, y)
            for x in range(cw)
            for y in range(ch)
            if out.getpixel((x, y))[:3] == (200, 150, 50)
        ]
        xs = [p[0] for p in char_pixels]
        ys = [p[1] for p in char_pixels]
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        self.assertAlmostEqual(cx, cw / 2, delta=1)
        self.assertAlmostEqual(cy, ch / 2, delta=1)
