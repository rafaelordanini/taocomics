"""
Gera um fluxograma visual e colorido do pipeline de criação do conto.
Desenhado programaticamente com Pillow para refletir com precisão o fluxo real
de agentes (Roteirista → Especialista → Designer → Artista → Revisor → ...).
"""
import os
import logging
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Cores dos agentes — espelham as variáveis do frontend (style.css)
COLOR_INPUT = (52, 73, 94)        # Azul-ardósia
COLOR_ROTEIRISTA = (142, 68, 173) # Roxo
COLOR_ESPECIALISTA = (26, 188, 156) # Verde turquesa
COLOR_DESIGNER = (230, 126, 34)   # Laranja
COLOR_ARTISTA = (41, 128, 185)    # Azul
COLOR_REVISOR = (39, 174, 96)     # Verde
COLOR_FINAL = (212, 175, 55)      # Dourado
COLOR_LOOP = (192, 57, 43)        # Vermelho (feedback/reprovação)

BG_TOP = (24, 21, 37)
BG_BOTTOM = (15, 13, 26)
TEXT_LIGHT = (245, 245, 250)
TEXT_DIM = (180, 180, 195)


def _load_font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _rounded_box(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _text_center(draw, cx, y, text, font, fill):
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    draw.text((cx - w / 2, y), text, font=font, fill=fill)


def _vertical_gradient(width, height, top, bottom):
    base = Image.new("RGB", (width, height), top)
    top_r, top_g, top_b = top
    bot_r, bot_g, bot_b = bottom
    for y in range(height):
        t = y / max(height - 1, 1)
        r = int(top_r + (bot_r - top_r) * t)
        g = int(top_g + (bot_g - top_g) * t)
        b = int(top_b + (bot_b - top_b) * t)
        for x in range(width):
            base.putpixel((x, y), (r, g, b))
    return base


def gerar_fluxograma(titulo: str, total_paginas: int, artista_model: str, output_path: str) -> str | None:
    """
    Cria um PNG com o fluxograma do pipeline e salva em output_path.
    Retorna o caminho salvo, ou None em caso de erro.
    """
    try:
        W, H = 900, 1500

        # Fundo em gradiente (mais leve do que putpixel por toda a área)
        img = Image.new("RGB", (W, H), BG_TOP)
        draw = ImageDraw.Draw(img)
        for y in range(H):
            t = y / (H - 1)
            r = int(BG_TOP[0] + (BG_BOTTOM[0] - BG_TOP[0]) * t)
            g = int(BG_TOP[1] + (BG_BOTTOM[1] - BG_TOP[1]) * t)
            b = int(BG_TOP[2] + (BG_BOTTOM[2] - BG_TOP[2]) * t)
            draw.line([(0, y), (W, y)], fill=(r, g, b))

        font_title = _load_font(38, bold=True)
        font_sub = _load_font(20)
        font_box = _load_font(24, bold=True)
        font_desc = _load_font(16)
        font_small = _load_font(15)

        # Cabeçalho
        _text_center(draw, W / 2, 36, "Fluxo de Criação do Conto", font_title, TEXT_LIGHT)
        titulo_curto = titulo if len(titulo) <= 48 else titulo[:45] + "..."
        _text_center(draw, W / 2, 86, f"“{titulo_curto}”", font_sub, COLOR_FINAL)

        # Definição das etapas do pipeline (refletem o fluxo real em agents.py)
        etapas = [
            (COLOR_INPUT, "Entrada do Conto", "Texto original enviado pelo usuário"),
            (COLOR_ROTEIRISTA, "Roteirista", "Adapta o texto em roteiro de HQ (páginas e painéis)"),
            (COLOR_ESPECIALISTA, "Especialista China", "Valida autenticidade taoísta do roteiro"),
            (COLOR_DESIGNER, "Designer Oriental", "Cria o prompt visual de cada página"),
            (COLOR_ARTISTA, "Artista", f"Desenha a página  ({artista_model})"),
            (COLOR_REVISOR, "Revisor", "Inspeciona imagem, textos e qualidade"),
            (COLOR_ESPECIALISTA, "Especialista China", "Confere fidelidade taoísta da página"),
            (COLOR_FINAL, "Conto Finalizado", f"{total_paginas} página(s) aprovada(s) e salvas"),
        ]

        # Geometria das caixas
        box_w = 540
        box_x = (W - box_w) / 2
        box_h = 96
        gap = 52
        start_y = 150

        centers = []
        for idx, (color, title, desc) in enumerate(etapas):
            y = start_y + idx * (box_h + gap)
            cx = W / 2
            cy = y + box_h / 2
            centers.append((cx, y, cy))

            # Sombra suave
            _rounded_box(draw, (box_x + 4, y + 5, box_x + box_w + 4, y + box_h + 5), 18, fill=(0, 0, 0))
            # Caixa principal
            _rounded_box(draw, (box_x, y, box_x + box_w, y + box_h), 18, fill=color)
            # Barra lateral de destaque
            _rounded_box(draw, (box_x, y, box_x + 10, y + box_h), 18, fill=color)

            _text_center(draw, cx, y + 22, title, font_box, TEXT_LIGHT)
            _text_center(draw, cx, y + 58, desc, font_desc, (245, 245, 250))

            # Seta para a próxima etapa
            if idx < len(etapas) - 1:
                ax = cx
                ay1 = y + box_h
                ay2 = y + box_h + gap
                draw.line([(ax, ay1), (ax, ay2 - 6)], fill=TEXT_DIM, width=4)
                draw.polygon(
                    [(ax - 9, ay2 - 12), (ax + 9, ay2 - 12), (ax, ay2 + 2)],
                    fill=TEXT_DIM,
                )

        # Loop de feedback: Revisor/Especialista -> Artista (reprovação faz redesenhar)
        artista_idx = 4
        revisor_idx = 5
        _, ay_art, acy_art = centers[artista_idx]
        _, ay_rev, acy_rev = centers[revisor_idx]
        loop_x = box_x + box_w + 48
        top_y = acy_art
        bot_y = acy_rev
        right_edge = box_x + box_w
        # Linha do Revisor saindo pela direita
        draw.line([(right_edge, bot_y), (loop_x, bot_y)], fill=COLOR_LOOP, width=4)
        draw.line([(loop_x, bot_y), (loop_x, top_y)], fill=COLOR_LOOP, width=4)
        draw.line([(loop_x, top_y), (right_edge, top_y)], fill=COLOR_LOOP, width=4)
        # Seta entrando no Artista
        draw.polygon(
            [(right_edge + 12, top_y - 9), (right_edge + 12, top_y + 9), (right_edge, top_y)],
            fill=COLOR_LOOP,
        )
        # Rótulo do loop
        loop_label = "reprovado:"
        loop_label2 = "redesenha"
        draw.text((loop_x + 10, (top_y + bot_y) / 2 - 20), loop_label, font=font_small, fill=COLOR_LOOP)
        draw.text((loop_x + 10, (top_y + bot_y) / 2 + 0), loop_label2, font=font_small, fill=COLOR_LOOP)

        # Rodapé
        _text_center(
            draw, W / 2, H - 40,
            "Pipeline multiagente · TaoComics",
            font_small, TEXT_DIM,
        )

        img.save(output_path, "PNG")
        return output_path
    except Exception as e:
        logger.error(f"[flowchart] Erro ao gerar fluxograma: {e}")
        return None
