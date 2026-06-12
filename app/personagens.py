"""
Biblioteca global de personagens — compartilhada entre TODOS os contos.

Personagens recorrentes (ex: Zhuangzi, o mestre taoísta) devem ter a MESMA
aparência em contos diferentes. A biblioteca guarda, por personagem:
  - descritor visual compacto em inglês (para o prompt do Artista)
  - recorte exato do rosto (PNG), extraído da primeira aparição aprovada

Estrutura em saved_comics/personagens/:
  biblioteca.json            -> {slug: {"nome": str, "descritor": str, "rosto": "slug_rosto.png"|None}}
  <slug>_rosto.png           -> recorte do rosto do personagem
"""
import os
import re
import json
import logging
import threading
import unicodedata
from PIL import Image

_lib_lock = threading.Lock()


def _lib_dir() -> str:
    # Resolve o diretório saved_comics sem importar agents (evita importação circular)
    for base in ["/tmp", os.path.expanduser("~"), "."]:
        path = os.path.join(base, "saved_comics", "personagens")
        try:
            os.makedirs(path, exist_ok=True)
            test = os.path.join(path, ".write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.remove(test)
            return path
        except Exception:
            continue
    raise RuntimeError("Nenhum diretório gravável para a biblioteca de personagens")


def _lib_json_path() -> str:
    return os.path.join(_lib_dir(), "biblioteca.json")


def slugify(nome: str) -> str:
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s-]+", "_", s) or "personagem"


def carregar_biblioteca() -> dict:
    try:
        with open(_lib_json_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def salvar_biblioteca(lib: dict):
    with _lib_lock:
        try:
            with open(_lib_json_path(), "w", encoding="utf-8") as f:
                json.dump(lib, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.warning(f"[personagens] Falha ao salvar biblioteca: {e}")


def extrair_personagens_do_roteiro(g_client, roteiro: dict) -> list:
    """Extrai do roteiro a lista de personagens com descritor visual compacto.
    Retorna [{"nome": str, "descritor": str}]. Em falha, lista vazia."""
    try:
        roteiro_txt = json.dumps(roteiro, ensure_ascii=False)[:6000]
        prompt = (
            "From this comic script (JSON), list the named/recurring characters. "
            "Answer ONLY with a strict JSON array, no prose:\n"
            '[{"nome": "character name in Portuguese", "descritor": "compact ENGLISH visual descriptor, '
            'max 25 words: age, face shape, beard/hair, clothing, body type"}]\n'
            "Include at most 5 characters. Ignore crowds/unnamed extras.\n\n"
            f"SCRIPT:\n{roteiro_txt}"
        )
        response = g_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
        )
        texto = (response.text or "").strip()
        inicio = texto.find("[")
        fim = texto.rfind("]")
        if inicio == -1 or fim == -1:
            return []
        dados = json.loads(texto[inicio:fim + 1])
        out = []
        for p in dados:
            nome = (p.get("nome") or "").strip()
            descritor = (p.get("descritor") or "").strip()
            if nome and descritor:
                out.append({"nome": nome, "descritor": descritor[:220]})
        return out[:5]
    except Exception as e:
        logging.warning(f"[personagens] Falha ao extrair personagens do roteiro: {e}")
        return []


def sincronizar_biblioteca(g_client, roteiro: dict, sse_send=None) -> dict:
    """Garante que todo personagem do roteiro existe na biblioteca global.
    Personagens já conhecidos REUTILIZAM descritor e rosto existentes (consistência
    entre contos). Retorna o subconjunto da biblioteca usado por este conto."""
    extraidos = extrair_personagens_do_roteiro(g_client, roteiro)
    if not extraidos:
        return {}

    lib = carregar_biblioteca()
    usados = {}
    for p in extraidos:
        slug = slugify(p["nome"])
        if slug in lib:
            if sse_send:
                rosto = "com rosto de referência" if lib[slug].get("rosto") else "ainda sem rosto"
                sse_send(f"[Sistema] Personagem reconhecido na biblioteca: {lib[slug]['nome']} ({rosto}).")
        else:
            lib[slug] = {"nome": p["nome"], "descritor": p["descritor"], "rosto": None}
            if sse_send:
                sse_send(f"[Sistema] Novo personagem adicionado à biblioteca: {p['nome']}.")
        usados[slug] = lib[slug]

    salvar_biblioteca(lib)
    return usados


def personagens_na_pagina(personagens: dict, pagina_script) -> dict:
    """Filtra os personagens cujo nome aparece no texto do roteiro da página."""
    texto = json.dumps(pagina_script, ensure_ascii=False).lower() if not isinstance(pagina_script, str) else pagina_script.lower()
    out = {}
    for slug, p in personagens.items():
        primeiro_nome = p["nome"].split()[0].lower()
        if p["nome"].lower() in texto or primeiro_nome in texto:
            out[slug] = p
    return out


def descritores_para_prompt(personagens: dict, max_personagens: int = 3) -> str:
    """Linha compacta de descritores para anexar ao prompt do Artista."""
    itens = list(personagens.values())[:max_personagens]
    if not itens:
        return ""
    partes = [f"{p['nome']}: {p['descritor']}" for p in itens]
    return " CHARACTERS (keep faces EXACTLY as in the attached face reference crops): " + "; ".join(partes) + "."


def rostos_de_referencia(personagens: dict, max_rostos: int = 3) -> list:
    """Retorna [(nome, caminho_png)] dos recortes de rosto disponíveis."""
    out = []
    base = _lib_dir()
    for p in personagens.values():
        if p.get("rosto"):
            path = os.path.join(base, p["rosto"])
            if os.path.exists(path):
                out.append((p["nome"], path))
        if len(out) >= max_rostos:
            break
    return out


def recortar_rosto(g_client, image: Image.Image, nome_personagem: str) -> Image.Image:
    """Pede ao Gemini a bounding box do rosto do personagem na imagem e recorta
    com PIL. Coordenadas normalizadas 0-1000 [ymin, xmin, ymax, xmax]."""
    try:
        prompt = (
            f"Find the FACE of the character '{nome_personagem}' in this comic page. "
            "Pick the largest/clearest depiction of this character's face. "
            "Answer ONLY with strict JSON, no prose:\n"
            '{"encontrado": true/false, "box_2d": [ymin, xmin, ymax, xmax]}\n'
            "box_2d uses coordinates normalized to 0-1000. The box must tightly contain "
            "the head (face + hair/headwear), not the whole body."
        )
        response = g_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[image, prompt],
        )
        texto = (response.text or "").strip()
        inicio = texto.find("{")
        fim = texto.rfind("}")
        if inicio == -1 or fim == -1:
            return None
        dados = json.loads(texto[inicio:fim + 1])
        if not dados.get("encontrado") or not dados.get("box_2d"):
            return None
        ymin, xmin, ymax, xmax = [max(0, min(1000, int(v))) for v in dados["box_2d"]]
        if ymax <= ymin or xmax <= xmin:
            return None
        w, h = image.size
        # Margem de 10% ao redor do rosto
        mx = int((xmax - xmin) * 0.1)
        my = int((ymax - ymin) * 0.1)
        box = (
            max(0, (xmin - mx) * w // 1000),
            max(0, (ymin - my) * h // 1000),
            min(w, (xmax + mx) * w // 1000),
            min(h, (ymax + my) * h // 1000),
        )
        crop = image.crop(box)
        # Descarta recortes minúsculos (provável erro de detecção)
        if crop.width < 40 or crop.height < 40:
            return None
        return crop
    except Exception as e:
        logging.warning(f"[personagens] Falha ao recortar rosto de '{nome_personagem}': {e}")
        return None


def salvar_rostos_da_pagina(g_client, image: Image.Image, personagens: dict, sse_send=None):
    """Para cada personagem da página ainda SEM rosto na biblioteca, recorta o
    rosto da imagem aprovada e salva o PNG na pasta da biblioteca."""
    lib = carregar_biblioteca()
    mudou = False
    for slug, p in personagens.items():
        if lib.get(slug, {}).get("rosto"):
            continue  # já tem rosto canônico — nunca sobrescreve
        crop = recortar_rosto(g_client, image, p["nome"])
        if crop is None:
            continue
        filename = f"{slug}_rosto.png"
        try:
            crop.save(os.path.join(_lib_dir(), filename))
            if slug not in lib:
                lib[slug] = dict(p)
            lib[slug]["rosto"] = filename
            p["rosto"] = filename
            mudou = True
            if sse_send:
                sse_send(f"[Sistema] ✓ Rosto canônico de '{p['nome']}' recortado e salvo na biblioteca de personagens ({filename}).")
        except Exception as e:
            logging.warning(f"[personagens] Falha ao salvar rosto de '{p['nome']}': {e}")
    if mudou:
        salvar_biblioteca(lib)
