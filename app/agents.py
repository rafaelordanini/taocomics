import os
import io
import time
import json
import logging

logging.basicConfig(level=logging.INFO)


def _get_saved_comics_dir() -> str:
    for base in ("/data", "/tmp"):
        try:
            path = os.path.join(base, "saved_comics")
            os.makedirs(path, exist_ok=True)
            test = os.path.join(path, ".write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.remove(test)
            return path
        except OSError:
            continue
    raise RuntimeError("Nenhum diretório gravável encontrado para saved_comics")
import base64
import httpx
from PIL import Image
from pydantic import BaseModel, Field
from typing import List, Callable, Any
from google import genai
from google.genai import types
import openai
import anthropic
from dotenv import load_dotenv

# Carrega chaves do .env por padrão
load_dotenv()

# Modelos Principais e Backups
GEMINI_PRO_MODEL = "gemini-2.5-pro"
OPENROUTER_TEXT_BACKUP_MODEL = "deepseek/deepseek-chat"  # DeepSeek V3 / V4 Pro Max
OPENROUTER_GEMINI_BACKUP_MODEL = "google/gemini-2.5-pro"
OPENROUTER_FLASH_BACKUP_MODEL = "google/gemini-2.5-flash"
OPENAI_ARTIST_MODEL = "gpt-image-2"
OPENROUTER_ARTIST_BACKUP_MODEL = "google/gemini-2.5-flash-image"

# Pydantic Schemas
class Quadrinho(BaseModel):
    quadrinho_numero: int = Field(description="Número do quadrinho nesta página")
    descricao_visual: str = Field(description="Descrição visual detalhada do cenário, personagens, ações e expressões.")
    texto: str = Field(description="Narrador e balões de diálogo dos personagens em português.")

class Pagina(BaseModel):
    pagina_numero: int = Field(description="Número da página (1 a 10)")
    quadrinhos: List[Quadrinho] = Field(description="Lista de 6 a 12 quadrinhos desta página.")

class RoteiroHQ(BaseModel):
    titulo: str = Field(description="Título da história taoísta.")
    total_paginas: int = Field(description="Total de páginas da HQ (1 a 10).")
    paginas: List[Pagina] = Field(description="Páginas da HQ.")


def clean_json_text(text: str) -> str:
    text = text.strip()
    
    # Try to find the JSON boundaries
    first_brace = text.find('{')
    first_bracket = text.find('[')
    
    start_idx = -1
    end_char = ''
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start_idx = first_brace
        end_char = '}'
    elif first_bracket != -1:
        start_idx = first_bracket
        end_char = ']'
        
    if start_idx != -1:
        end_idx = text.rfind(end_char)
        if end_idx != -1 and end_idx > start_idx:
            return text[start_idx:end_idx + 1].strip()
            
    # Fallback to standard stripping if no braces/brackets are found
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()



def run_with_retry(
    agent_name: str,
    primary_fn: Callable[..., Any],
    fallback_fn: Callable[..., Any],
    sse_send: Callable[[str], None],
    *args,
    **kwargs
) -> Any:
    try:
        sse_send(f"[{agent_name}] Tentando usar IA principal...")
        result = primary_fn(*args, **kwargs)
        sse_send(f"[{agent_name}] Sucesso usando IA principal!")
        return result
    except Exception as e:
        sse_send(f"[{agent_name}] Falha na IA principal: {str(e)}")
        sse_send(f"[{agent_name}] Ativando IA de Backup (OpenRouter)...")
        try:
            result = fallback_fn(*args, **kwargs)
            sse_send(f"[{agent_name}] Sucesso usando IA de Backup (OpenRouter)!")
            return result
        except Exception as eb:
            sse_send(f"[{agent_name}] ERRO CRÍTICO: IA de Backup também falhou: {str(eb)}")
            raise RuntimeError(f"Ambas IAs falharam. Erro primário: {str(e)}. Erro backup: {str(eb)}")


def extract_reasoning(response, is_gemini=False) -> str:
    if is_gemini:
        try:
            candidate = response.candidates[0]
            if hasattr(candidate, "thinking_process") and candidate.thinking_process:
                if hasattr(candidate.thinking_process, "text") and candidate.thinking_process.text:
                    return candidate.thinking_process.text
                elif hasattr(candidate.thinking_process, "parts"):
                    return "".join([part.text for part in candidate.thinking_process.parts if hasattr(part, "text")])
        except Exception:
            pass
        return None
    else:
        try:
            message = response.choices[0].message
            if hasattr(message, "reasoning_content") and message.reasoning_content:
                return message.reasoning_content
            if hasattr(message, "model_extra") and isinstance(message.model_extra, dict):
                return message.model_extra.get("reasoning_content") or message.model_extra.get("reasoning")
        except Exception:
            pass
        return None


def extract_content_from_openai(response) -> str:
    if not response.choices:
        raise RuntimeError("A API (OpenRouter/OpenAI) retornou uma lista de escolhas vazia.")
    content = response.choices[0].message.content
    if content is None:
        finish_reason = getattr(response.choices[0], "finish_reason", "unknown")
        raise RuntimeError(f"A API retornou conteúdo vazio (None). Finish reason: {finish_reason}")
    return content.strip()


def extract_content_from_gemini(response) -> str:
    try:
        text = response.text
    except Exception as e:
        raise RuntimeError(f"Falha ao obter o texto da resposta do Gemini: {str(e)}")
    if text is None:
        raise RuntimeError("O texto da resposta do Gemini retornou None.")
    return text.strip()

def resize_and_pad_image_to_target(image: Image.Image, target_width: int = 1024, target_height: int = 1536, is_page_1: bool = False) -> Image.Image:
    """
    Redimensiona a imagem mantendo a proporção original e corta os excessos (crop)
    para preencher completamente as dimensões especificadas (por padrão 1024x1536),
    garantindo que não haja faixas pretas nas laterais (pillarboxing) ou no topo/rodapé (letterboxing).
    Se for a página 1 (contendo o título principal no topo), o corte vertical preserva o topo
    (corta apenas 10% do excesso no topo e 90% no rodapé), evitando cortar o título.
    """
    orig_width, orig_height = image.size
    if orig_width == target_width and orig_height == target_height:
        return image

    # Calcula a escala para cobrir toda a área de destino (estilo crop/cobertura total)
    ratio = max(target_width / orig_width, target_height / orig_height)
    new_width = int(orig_width * ratio)
    new_height = int(orig_height * ratio)

    # Redimensiona a imagem usando filtro de alta qualidade
    resized_image = image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Corta para obter as dimensões exatas
    left = (new_width - target_width) // 2
    
    if is_page_1:
        # Se for a página 1, preserva o topo (corta apenas 10% do excesso no topo e o resto embaixo)
        excess_height = new_height - target_height
        top = int(excess_height * 0.10)
    else:
        # Nas demais páginas, faz corte centralizado padrão (50% topo, 50% rodapé)
        top = (new_height - target_height) // 2
        
    right = left + target_width
    bottom = top + target_height

    return resized_image.crop((left, top, right, bottom))


# --------------------------
# Helpers de Arquivos
# --------------------------

def _processar_e_descrever_arquivo(g_client, b64: str = None, mime: str = None, agent_name: str = "Agente") -> str:
    """Usa o Gemini para extrair texto/descrever um arquivo anexado para uso em modelos text-only."""
    if not b64 or not mime or not g_client:
        return ""
    try:
        import base64
        file_bytes = base64.b64decode(b64)
        part = types.Part.from_bytes(data=file_bytes, mime_type=mime)
        
        prompt = (
            f"Extraia o texto completo se for um PDF/documento ou descreva detalhadamente a imagem/diagrama fornecido. "
            f"Foque em detalhes relevantes que sirvam de referência ou instruções adicionais de design/conteúdo "
            f"para o agente '{agent_name}' na elaboração de uma HQ de contos taoístas. "
            f"Responda apenas com a extração ou descrição direta, sem enrolações."
        )
        response = g_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[part, prompt]
        )
        desc = response.text.strip()
        return f"\n\n[CONTEÚDO DO DOCUMENTO/IMAGEM DE EXEMPLO ANEXADO PARA REFERÊNCIA]:\n{desc}"
    except Exception as e:
        return f"\n\n[Erro ao processar arquivo anexado para {agent_name}: {str(e)}]"


def _try_extract_pdf_text(b64: str) -> str:
    """Extrai texto de um arquivo PDF codificado em Base64 usando pypdf."""
    try:
        import pypdf
        import base64
        file_bytes = base64.b64decode(b64)
        reader = pypdf.PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
        return text.strip()
    except Exception as e:
        return f"[Erro ao ler PDF: {str(e)}]"


def _encode_pil_to_base64(image: Image.Image) -> str:
    buffered = io.BytesIO()
    if image.mode in ("RGBA", "P"):
        image = image.convert("RGB")
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def _status_parecer(texto: str) -> str:
    """Extrai o status do parecer para uso no nome do arquivo: aprovado, aprovado_com_ressalvas ou reprovado."""
    upper = texto.upper()
    if "APROVADO COM RESSALVAS" in upper or "APROVADA COM RESSALVAS" in upper or "AUTORIZADO COM AJUSTES" in upper:
        return "aprovado_com_ressalvas"
    if "REPROVADO" in upper or "REPROVADA" in upper or "NÃO AUTORIZADO" in upper or "NÃO AUTORIZADA" in upper:
        return "reprovado"
    if "APROVADO" in upper or "APROVADA" in upper or "AUTORIZADO" in upper:
        return "aprovado"
    return "sem_status"


def _salvar_imagem_rejeitada(imagem_ou_path, tale_dir: str, page_num: int, suffix: str, model_id: str = None):
    try:
        rejeitadas_dir = os.path.join(tale_dir, "artista", "rejeitadas")
        os.makedirs(rejeitadas_dir, exist_ok=True)
        timestamp = int(time.time())
        filename = f"pagina_{page_num}_{suffix}_{timestamp}.png"
        filepath = os.path.join(rejeitadas_dir, filename)
        if isinstance(imagem_ou_path, str):
            if os.path.exists(imagem_ou_path):
                import shutil
                shutil.copy2(imagem_ou_path, filepath)
        else:
            imagem_ou_path.save(filepath)
            
        if model_id:
            _registrar_modelo_utilizado(tale_dir, filename, "rejeitada", model_id)
    except Exception as e:
        print(f"Erro ao salvar imagem rejeitada: {e}")



def _format_openrouter_payload(
    prompt: str,
    system_instruction: str = None,
    images: List[Image.Image] = None,
    attachments: List[tuple[str, str]] = None
) -> list:
    """
    Formata mensagens em formato de chat compatível com OpenAI/OpenRouter vision.
    Suporta imagens em PIL e anexos (base64 + mime).
    """
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
        
    content_list = []
    has_image = False
    
    # Adiciona imagens PIL codificadas em base64
    if images:
        for img in images:
            if img is None:
                continue
            buffered = io.BytesIO()
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(buffered, format="JPEG", quality=85)
            img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
            content_list.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{img_str}"
                }
            })
            has_image = True
            
    # Adiciona anexos adicionais (imagens, texto, ou PDF)
    if attachments:
        for b64, mime in attachments:
            if not b64 or not mime:
                continue
            if mime.startswith("image/"):
                content_list.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime};base64,{b64}"
                    }
                })
                has_image = True
            elif "pdf" in mime:
                pdf_text = _try_extract_pdf_text(b64)
                if pdf_text:
                    prompt += f"\n\n[CONTEÚDO DO DOCUMENTO PDF DE EXEMPLO ANEXADO]:\n{pdf_text}"
            else:
                # Trata como texto e decodifica
                try:
                    text_content = base64.b64decode(b64).decode("utf-8")
                    prompt += f"\n\n[CONTEÚDO DO DOCUMENTO ANEXADO]:\n{text_content}"
                except Exception:
                    pass
                    
    if has_image:
        content_list.append({"type": "text", "text": prompt})
        messages.append({"role": "user", "content": content_list})
    else:
        messages.append({"role": "user", "content": prompt})
        
    return messages



def map_model_id_to_friendly_name(model_id: str) -> str:
    if not model_id:
        return "desconhecido"
    model_id_lower = model_id.lower()
    if "codex" in model_id_lower:
        return "codex gpt"
    elif "poe" in model_id_lower:
        return "poe gpt"
    elif "openrouter" in model_id_lower and "gpt" in model_id_lower:
        return "openrouter gpt"
    elif "openrouter" in model_id_lower and "gemini" in model_id_lower:
        return "openrouter gemini"
    elif "google/imagen" in model_id_lower:
        return "google studio imagen"
    elif "google/gemini" in model_id_lower:
        return "google studio gemini"
    elif "openai/gpt" in model_id_lower:
        return "openai api gpt"
    elif "pollinations" in model_id_lower:
        return "pollinations flux"
    return model_id

def _registrar_modelo_utilizado(tale_dir: str, filename: str, status: str, model_id: str):
    try:
        friendly_name = map_model_id_to_friendly_name(model_id)
        registro_path = os.path.join(tale_dir, "artista", "registro_ias.txt")
        os.makedirs(os.path.dirname(registro_path), exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = f"{timestamp} - {filename} ({status}): {friendly_name}\n"
        with open(registro_path, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"Erro ao registrar modelo utilizado: {e}")


def _drive_upload(path: str):
    try:
        from app.drive_upload import upload_file_to_drive
        upload_file_to_drive(path)
    except Exception:
        pass


def _run_codex_text(prompt: str) -> str:
    worker_url = os.environ.get("CODEX_WORKER_URL", "").rstrip("/")
    worker_token = os.environ.get("CODEX_WORKER_TOKEN", "")

    if not worker_url:
        raise RuntimeError(
            "CODEX_WORKER_URL não configurado."
        )

    headers = {}
    if worker_token:
        headers["Authorization"] = f"Bearer {worker_token}"

    response = httpx.post(
        f"{worker_url}/run-text",
        json={"prompt": prompt},
        headers=headers,
        timeout=300.0
    )

    if response.status_code != 200:
        raise RuntimeError(f"Codex worker retornou erro {response.status_code}: {response.text[:300]}")

    return response.json()["text"]

def _run_poe_text(prompt: str, model: str = "Claude-3.5-Sonnet", image: Image.Image = None, api_key: str = None) -> str:
    import openai
    key = api_key or os.getenv("POE_API_KEY") or "sk-poe-5dU7XMSEIjUgZsYWFt-n47g5GwOgXazF7b0k95TYATk"
    client = openai.OpenAI(
        api_key=key,
        base_url="https://api.poe.com/v1"
    )
    
    messages = []
    if image:
        b64 = _encode_pil_to_base64(image)
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{b64}"
                    }
                }
            ]
        })
    else:
        messages.append({"role": "user", "content": prompt})
        
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        timeout=120.0
    )
    return response.choices[0].message.content.strip()

def _executar_agente_texto_visao(
    agent_name: str,
    prompt: str,
    system_instruction: str = None,
    image: Image.Image = None,
    custom_keys: dict = None,
    sse_send: Callable[[str], None] = None,
    fallback_or_fn: Callable[[], dict] = None
) -> dict:
    keys = custom_keys or {}
    last_error = None
    
    # 1. Tentar Codex
    try:
        if not os.environ.get("CODEX_WORKER_URL"):
            raise FileNotFoundError("CODEX_WORKER_URL não configurado")
            
        if sse_send:
            sse_send(f"[{agent_name}] Usando o modelo: Codex (gpt-5.5)")
        else:
            print(f"[{agent_name}] Tentando usar Codex CLI...")
            
        temp_img_path = None
        if image:
            os.makedirs(os.path.expanduser("~/.codex"), exist_ok=True)
            temp_img_path = os.path.expanduser(f"~/.codex/temp_vision_{int(time.time())}.png")
            image.save(temp_img_path)
            
        MAX_CODEX_PROMPT = 800
        prompt_codex = prompt[:MAX_CODEX_PROMPT] if len(prompt) > MAX_CODEX_PROMPT else prompt

        full_prompt = ""
        if system_instruction:
            full_prompt += f"System Instruction (Context/Identity):\n{system_instruction}\n\n"
        full_prompt += f"Task Prompt:\n{prompt_codex}"
        if temp_img_path:
            full_prompt += f"\n\nPlease analyze the image located at this file path: {temp_img_path}"

        content = _run_codex_text(full_prompt)
        
        if temp_img_path and os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
            except Exception:
                pass
                
        msg_succ = f"[{agent_name}] Sucesso usando Codex CLI!"
        if sse_send:
            sse_send(msg_succ)
        else:
            print(msg_succ)
        return {"content": content, "reasoning": None}
    except Exception as e:
        msg_fail = f"[{agent_name}] Codex CLI falhou ou indisponível: {str(e)}"
        if sse_send:
            sse_send(msg_fail)
        else:
            print(msg_fail)
        last_error = e

    # 2. Tentar Poe
    try:
        poe_models = ["Claude-3.5-Sonnet", "Claude-3-5-Sonnet", "GPT-4o"]
        poe_content = None
        poe_err = None
        for poe_mod in poe_models:
            try:
                if sse_send:
                    sse_send(f"[{agent_name}] Usando o modelo: Poe ({poe_mod})")
                else:
                    print(f"[{agent_name}] Tentando usar Poe API ({poe_mod})...")
                poe_prompt = prompt
                if system_instruction:
                    poe_prompt = f"System Instruction:\n{system_instruction}\n\nPrompt:\n{prompt}"
                poe_content = _run_poe_text(
                    prompt=poe_prompt,
                    model=poe_mod,
                    image=image,
                    api_key=keys.get("poe_api_key")
                )
                if poe_content:
                    break
            except Exception as pe:
                poe_err = pe
                
        if not poe_content:
            raise poe_err or RuntimeError("Poe API falhou com todos os modelos.")
            
        msg_succ = f"[{agent_name}] Sucesso usando Poe API!"
        if sse_send:
            sse_send(msg_succ)
        else:
            print(msg_succ)
        return {"content": poe_content, "reasoning": None}
    except Exception as e:
        msg_fail = f"[{agent_name}] Poe API falhou: {str(e)}"
        if sse_send:
            sse_send(msg_fail)
        else:
            print(msg_fail)
        last_error = e

    # 3. Tentar OpenRouter / Gemini
    if fallback_or_fn:
        msg_try = f"[{agent_name}] Executando fallback nativo/OpenRouter..."
        if sse_send:
            sse_send(msg_try)
        else:
            print(msg_try)
        return fallback_or_fn()
        
    raise last_error or RuntimeError("Todos os canais de IA falharam.")

def _generar_imagem_poe(prompt: str, api_key: str = None) -> dict:
    import openai
    import re
    key = api_key or os.getenv("POE_API_KEY") or "sk-poe-5dU7XMSEIjUgZsYWFt-n47g5GwOgXazF7b0k95TYATk"
    client = openai.OpenAI(
        api_key=key,
        base_url="https://api.poe.com/v1"
    )
    
    full_prompt = f"Generate an image: {prompt}. Image size must be 1024x1536. Please provide the image."
    
    response = client.chat.completions.create(
        model="gpt-image-2",
        messages=[{"role": "user", "content": full_prompt}],
        timeout=120.0
    )
    
    content = response.choices[0].message.content or ""
    match = re.search(r"!\[.*?\]\((https?://[^\)]+)\)", content)
    if not match:
        match = re.search(r"(https?://[^\s\)]+)", content)
        
    if not match:
        raise ValueError(f"Não foi possível encontrar a URL da imagem na resposta do Poe. Resposta: {content}")
        
    img_url = match.group(1)
    
    import httpx
    img_resp = httpx.get(img_url, timeout=60.0)
    img_resp.raise_for_status()
    
    b64_data = base64.b64encode(img_resp.content).decode("utf-8")
    return {
        "url": img_url,
        "b64_json": b64_data
    }


# --------------------------
# 1. Agente Roteirista
# --------------------------


def _roteirista_primary(client, conto: str, geral: str = None, especifica: str = None, arquivo_b64: str = None, arquivo_mime: str = None, g_client = None, sse_send: Callable[[str], None] = None) -> dict:
    instructions_addition = ""
    if geral:
        instructions_addition += f"\nGeneral instructions: {geral}"
    if especifica:
        instructions_addition += f"\nSpecific instruction (ABSOLUTE PRIORITY): {especifica}. Override other rules if conflicting."
    if arquivo_b64 and arquivo_mime and g_client:
        instructions_addition += _processar_e_descrever_arquivo(g_client, arquivo_b64, arquivo_mime, "Roteirista")
        
    prompt = (
        f"Transforme o conto taoísta abaixo em um roteiro estruturado de HQ de 1 a 10 páginas (com 6 a 12 quadrinhos por página). "
        f"Você deve responder APENAS com um objeto JSON válido seguindo a estrutura exata abaixo, sem markdown, sem explicações extras:\n\n"
        f"Estrutura JSON esperada:\n"
        f"{{\n"
        f"  \"titulo\": \"Nome do Conto\",\n"
        f"  \"total_paginas\": 3,\n"
        f"  \"paginas\": [\n"
        f"    {{\n"
        f"      \"pagina_numero\": 1,\n"
        f"      \"quadrinhos\": [\n"
        f"        {{\n"
        f"          \"quadrinho_numero\": 1,\n"
        f"          \"descricao_visual\": \"Descrição da cena...\",\n"
        f"          \"texto\": \"Narrador: ... / Personagem: ...\"\n"
        f"        }}\n"
        f"      ]\n"
        f"    }}\n"
        f"  ]\n"
        f"}}\n\n"
        f"Conto Taoísta:\n{conto}{instructions_addition}"
    )
    backup_models = [
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4.6"
    ]
    last_error = None
    for model in backup_models:
        try:
            if sse_send:
                sse_send(f"[Roteirista] Usando o modelo: OpenRouter ({model})")
            print(f"[Roteirista Fallback] Tentando usar IA de Backup ({model}) no OpenRouter...")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Você é um roteirista profissional de HQs. Responda apenas com o JSON puro, sem markdown."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=4096
            )
            content = extract_content_from_openai(response)
            print(f"[Roteirista Fallback] Sucesso usando IA de Backup ({model}) no OpenRouter!")
            return {
                "content": content,
                "reasoning": extract_reasoning(response, is_gemini=False)
            }
        except Exception as e:
            print(f"[Roteirista Fallback] Falha com IA de Backup ({model}): {str(e)}")
            last_error = e
    raise last_error or RuntimeError("Todos os modelos de backup do Roteirista no OpenRouter falharam.")

def _roteirista_fallback(client, conto: str, geral: str = None, especifica: str = None, arquivo_b64: str = None, arquivo_mime: str = None, sse_send: Callable[[str], None] = None) -> dict:
    prompt = f"Transforme o seguinte conto antigo taoísta em um roteiro estruturado de HQ:\n\n{conto}"
    if geral:
        prompt += f"\n\nINSTRUÇÕES GERAIS DO USUÁRIO PARA O ROTEIRO:\n{geral}"
    if especifica:
        prompt += f"\n\nINSTRUÇÃO ESPECÍFICA DO USUÁRIO (PRIORIDADE ABSOLUTA - Suplanta as gerais e regras básicas se houver conflito):\n{especifica}\nSiga esta regra à risca ao definir o total de páginas e quadrinhos!"
    if arquivo_b64 and arquivo_mime:
        prompt += _processar_e_descrever_arquivo(client, arquivo_b64, arquivo_mime, "Roteirista")
        
    system_instruction = (
        "Regra inicial: Verificar se o titulo está presente no conto. Se estiver, esse será o título definitivo. Se não tiver, eu inventarei um bom título. Todos os agentes devem respeitar o título estabelecido.\n\n"
        "Você é um roteirista de histórias em quadrinhos experiente. Sua tarefa é transformar contos taoístas antigos em roteiros detalhados de HQ de 1 a 10 páginas (dependendo do tamanho da história) com 6 a 12 quadrinhos por página. "
        "Para cada quadrinho, descreva minuciosamente a cena visualmente (cenário, ações, expressões, elementos orientais) e forneça a narração e balões de diálogo em português. "
        "Responda apenas com o JSON puro, sem markdown."
    )
    
    def native_fallback():
        contents = []
        if arquivo_b64 and arquivo_mime:
            import base64
            file_bytes = base64.b64decode(arquivo_b64)
            part = types.Part.from_bytes(data=file_bytes, mime_type=arquivo_mime)
            contents.append(part)
        contents.append(prompt)
        if sse_send:
            sse_send(f"[Roteirista] Usando o modelo: Google GenAI ({GEMINI_PRO_MODEL})")
        response = client.models.generate_content(
            model=GEMINI_PRO_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=RoteiroHQ,
                thinking_config=types.ThinkingConfig(thinking_budget=2048),
                system_instruction=system_instruction
            )
        )
        content = extract_content_from_gemini(response)
        return {
            "content": content,
            "reasoning": extract_reasoning(response, is_gemini=True)
        }
        
    return _executar_agente_texto_visao(
        agent_name="Roteirista",
        prompt=prompt,
        system_instruction=system_instruction,
        sse_send=sse_send,
        fallback_or_fn=native_fallback
    )




# --------------------------
# 2. Agente Designer Oriental
# --------------------------

def _designer_primary(
    client,
    pagina_script: dict,
    num_pagina: int,
    total_paginas: int,
    ref_image: Image.Image = None,
    geral: str = None,
    especifica: str = None,
    artista_geral: str = None,
    artista_especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    art_arquivo_b64: str = None,
    art_arquivo_mime: str = None,
    titulo: str = None,
    sse_send: Callable[[str], None] = None
) -> dict:
    script_str = json.dumps(pagina_script, ensure_ascii=False, indent=2)
    
    prompt = (
        f"Script da página {num_pagina} de {total_paginas}:\n{script_str}\n\n"
    )
    if ref_image:
        prompt += (
            "INSTRUÇÃO IMPORTANTE DE ESTILO: Analise a imagem de referência anexa. "
            "Você deve extrair o estilo artístico, paleta de cores, disposição dos painéis e "
            "o padrão/design de escrita do título (e das caixas de texto em geral) desta imagem modelo. "
            "Crie o prompt de geração de imagem in inglês de forma que o gerador de imagem (DALL-E 3) siga esse mesmo padrão visual "
            "e estilo de título/layout ao desenhar a página atual."
        )
    else:
        prompt += "Crie o prompt de imagem em inglês baseado nas instruções de estilo clássico em nanquim chinesa."
        
    if geral:
        prompt += f"\nINSTRUÇÕES GERAIS DO DESIGNER ORIENTAL:\n{geral}"
    if especifica:
        prompt += f"\nINSTRUÇÃO ESPECÍFICA DO DESIGNER ORIENTAL (PRIORIDADE ABSOLUTA - Suplanta as gerais e regras básicas se houver conflito):\n{especifica}"
        
    if artista_geral:
        prompt += (
            f"\nINSTRUÇÕES GERAIS DE DESENHO PARA O ARTISTA:\n{artista_geral}. "
            "Traduza e incorpore estas diretrizes de estilo no prompt final criado para o Artista."
        )
    if artista_especifica:
        prompt += (
            f"\nINSTRUÇÃO ESPECÍFICA DE DESENHO PARA O ARTISTA (PRIORIDADE ABSOLUTA - Suplanta a geral do artista se houver conflito):\n{artista_especifica}. "
            "Traduza e aplique esta regra prioritária no prompt final criado para o Artista."
        )
        
    system_instruction = (
        "Você é um designer de arte oriental tradicional e especialista em quadrinhos asiáticos (manhua, manga, paintings clássicas de nanquim e guache taoístas). "
        "Sua tarefa é receber o roteiro de uma página específica de um quadrinho taoísta e criar um prompt de geração de imagem detalhado e otimizado em inglês para essa página inteira. "
        "O prompt deve instruir o gerador a criar uma página de quadrinhos com vários painéis, mantendo a consistência de estilo. "
        "Instruções de estilo base: Pintura chinesa em nanquim (traditional Chinese ink wash painting style), traços fluidos de pincel, cores suaves e místicas, elements da natureza (névoa, montanhas, bambus), atmosfera espiritual e filosófica. "
        "O prompt deve descrever detalhadamente o layout dos quadrinhos (ex: 'A comic book page with 6 panels in traditional Chinese ink wash painting style...'). "
        "Instruções Especiais:\n"
    )
    if titulo:
        system_instruction += f"- Se for a primeira página (página número {num_pagina} igual a 1), inclua no prompt que o título principal da HQ deve ser EXATAMENTE '{titulo}' (ex: 'with a beautiful, stylized title banner at the top of the page with the exact text \"{titulo}\" written in Portuguese'). Não altere de forma alguma o nome do título.\n"
    else:
        system_instruction += f"- Se for a primeira página (página número {num_pagina} igual a 1), inclua no prompt que deve haver um título elegante escrito no topo (ex: 'with a beautiful, stylized title banner at the top of the page').\n"
        
    system_instruction += (
        f"- Se for a página final (página número {num_pagina} igual ao total {total_paginas}), descreva uma arte própria e poética que evidencie claramente o fim de um conto, com elementos que simbolizam o encerramento espiritual.\n"
        "Responda apenas com o prompt em inglês, sem outras explicações ou markdown."
    )
    
    def native_fallback():
        if sse_send:
            sse_send("[Designer Oriental] Usando o modelo: Google GenAI (gemini-2.5-flash)")
        contents = []
        if ref_image:
            contents.append(ref_image)
        if arquivo_b64 and arquivo_mime:
            import base64
            part = types.Part.from_bytes(data=base64.b64decode(arquivo_b64), mime_type=arquivo_mime)
            contents.append(part)
        if art_arquivo_b64 and art_arquivo_mime:
            import base64
            part = types.Part.from_bytes(data=base64.b64decode(art_arquivo_b64), mime_type=art_arquivo_mime)
            contents.append(part)
            
        contents.append(prompt)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                max_output_tokens=2048
            )
        )
        content = extract_content_from_gemini(response)
        return {
            "content": content,
            "reasoning": extract_reasoning(response, is_gemini=True)
        }
        
    prompt_with_files = prompt
    if arquivo_b64 and arquivo_mime:
        prompt_with_files += _processar_e_descrever_arquivo(client, arquivo_b64, arquivo_mime, "Designer Oriental")
    if art_arquivo_b64 and art_arquivo_mime:
        prompt_with_files += _processar_e_descrever_arquivo(client, art_arquivo_b64, art_arquivo_mime, "Artista")
        
    return _executar_agente_texto_visao(
        agent_name="Designer Oriental",
        prompt=prompt_with_files,
        system_instruction=system_instruction,
        image=ref_image,
        sse_send=sse_send,
        fallback_or_fn=native_fallback
    )



def _designer_fallback(
    client,
    pagina_script: dict,
    num_pagina: int,
    total_paginas: int,
    ref_image: Image.Image = None,
    geral: str = None,
    especifica: str = None,
    artista_geral: str = None,
    artista_especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    art_arquivo_b64: str = None,
    art_arquivo_mime: str = None,
    g_client = None,
    titulo: str = None,
    sse_send: Callable[[str], None] = None
) -> dict:
    script_str = json.dumps(pagina_script, ensure_ascii=False, indent=2)
    instructions_addition = f"\nDesigner general instructions: {geral}" if geral else ""
    specific_addition = f"\nDesigner specific (priority) instruction: {especifica}" if especifica else ""
    artist_gen_addition = f"\nArtist general instructions: {artista_geral}" if artista_geral else ""
    artist_spec_addition = f"\nArtist specific (priority) instruction: {artista_especifica}" if artista_especifica else ""
    
    if titulo:
        title_line = f"- Se for página 1, adicione o título do roteiro que deve ser EXATAMENTE '{titulo}' destacado no topo da imagem ('with a stylized title banner at the top with the exact text \"{titulo}\" written in Portuguese').\n"
    else:
        title_line = f"- Se for página 1, adicione um título destacado no topo ('with a stylized title banner at the top').\n"
        
    prompt = (
        f"Você é um designer de arte oriental e especialista em manhua/pintura em nanquim. "
        f"Gere um prompt de imagem em inglês para a página {num_pagina} (total de páginas: {total_paginas}) com o seguinte roteiro de quadrinhos:\n\n{script_str}\n\n"
        f"Diretrizes:\n"
        f"- Estilo: Traditional Chinese ink wash painting, fluid brushstrokes, mystical colors, mist, taoist atmosphere.\n"
        f"- Layout: Comic book page layout with panels.\n"
        f"{title_line}"
        f"- Se for a última página, adicione elementos que mostrem poeticamente o fim do conto ('indicates the poetic end of a tale').\n"
        f"Nota de estilo: Siga um padrão estético de títulos e ilustrações harmoniosas clássicas.{instructions_addition}{specific_addition}{artist_gen_addition}{artist_spec_addition}\n\n"
        f"Responda APENAS com o prompt em inglês."
    )
    
    attachments = []
    if arquivo_b64 and arquivo_mime:
        attachments.append((arquivo_b64, arquivo_mime))
    if art_arquivo_b64 and art_arquivo_mime:
        attachments.append((art_arquivo_b64, art_arquivo_mime))
        
    messages = _format_openrouter_payload(
        prompt=prompt,
        system_instruction="Você é um designer oriental. Escreva apenas o prompt em inglês.",
        images=[ref_image] if ref_image else None,
        attachments=attachments
    )
    
    backup_models = [
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4.6"
    ]
    last_error = None
    for model in backup_models:
        try:
            if sse_send:
                sse_send(f"[Designer Oriental] Usando o modelo: OpenRouter ({model})")
            print(f"[Designer Fallback] Tentando usar IA de Backup ({model}) no OpenRouter...")
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=2048
            )
            content = extract_content_from_openai(response)
            if sse_send:
                sse_send(f"[Designer Oriental] Sucesso usando o modelo: OpenRouter ({model})!")
            print(f"[Designer Fallback] Sucesso usando IA de Backup ({model}) no OpenRouter!")
            return {
                "content": content,
                "reasoning": extract_reasoning(response, is_gemini=False)
            }
        except Exception as e:
            print(f"[Designer Fallback] Falha com IA de Backup ({model}): {str(e)}")
            last_error = e
    raise last_error or RuntimeError("Todos os modelos de backup do Designer Oriental no OpenRouter falharam.")


# --------------------------
# 3. Agente Artista
# --------------------------

def _generar_imagem_codex(prompt: str, modelos_paths: list = None) -> dict:
    worker_url = os.environ.get("CODEX_WORKER_URL", "").rstrip("/")
    worker_token = os.environ.get("CODEX_WORKER_TOKEN", "")
    logging.info(f"[codex-worker] CODEX_WORKER_URL={worker_url!r}")

    if not worker_url:
        raise RuntimeError(
            "CODEX_WORKER_URL não configurado. "
            "Defina a variável de ambiente com a URL do codex-worker (ex: http://34.58.184.49:8080)."
        )

    headers = {}
    if worker_token:
        headers["Authorization"] = f"Bearer {worker_token}"

    response = httpx.post(
        f"{worker_url}/generate-image",
        json={"prompt": prompt},
        headers=headers,
        timeout=3700.0
    )

    if response.status_code != 200:
        raise RuntimeError(f"Codex worker retornou erro {response.status_code}: {response.text[:300]}")

    data = response.json()
    return {"url": None, "b64_json": data["b64_json"]}


def _artista_primary(client, prompt: str, g_client=None, model_name: str = "openai/gpt-image-2", poe_key: str = None, modelos_paths: list = None) -> dict:
    if model_name == "codex/gpt-image-2":
        return _generar_imagem_codex(prompt, modelos_paths=modelos_paths)
    elif model_name == "poe/gpt-image-2":
        return _generar_imagem_poe(prompt, api_key=poe_key)
    elif model_name == "openai/gpt-image-2":
        response = client.images.generate(
            model=OPENAI_ARTIST_MODEL,
            prompt=prompt,
            n=1,
            size="1024x1792",
            timeout=120.0
        )
        item = response.data[0]
        return {
            "url": getattr(item, "url", None),
            "b64_json": getattr(item, "b64_json", None)
        }
    elif model_name == "google/gemini-2.5-flash-image":
        if not g_client:
            raise ValueError("Cliente do Gemini não inicializado.")
        response = g_client.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"]
            )
        )
        for part in response.parts:
            if part.inline_data:
                return {
                    "url": None,
                    "b64_json": base64.b64encode(part.inline_data.data).decode("utf-8")
                }
        raise ValueError("Nenhum dado de imagem retornado pelo Gemini 2.5 Flash Image.")
    elif model_name == "google/imagen-4.0-generate-001":
        if not g_client:
            raise ValueError("Cliente do Gemini não inicializado.")
        response = g_client.models.generate_images(
            model="imagen-4.0-generate-001",
            prompt=prompt,
            config=types.GenerateImagesConfig(
                number_of_images=1,
                output_mime_type="image/png",
                aspect_ratio="9:16"
            )
        )
        if response.generated_images:
            img_bytes = response.generated_images[0].image.image_bytes
            return {
                "url": None,
                "b64_json": base64.b64encode(img_bytes).decode("utf-8")
            }
        raise ValueError("Nenhum dado de imagem retornado pelo Imagen 3.")
    elif model_name == "pollinations/flux":
        p_key = os.getenv("POLLINATIONS_API_KEY")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        if p_key:
            headers["Authorization"] = f"Bearer {p_key}"
        
        data = {
            "prompt": prompt,
            "model": "flux",
            "size": "1024x1536",
            "n": 1
        }
        response = httpx.post("https://gen.pollinations.ai/v1/images/generations", headers=headers, json=data, timeout=60.0)
        if response.status_code == 200:
            res_json = response.json()
            if "data" in res_json and len(res_json["data"]) > 0:
                item = res_json["data"][0]
                if "b64_json" in item and item["b64_json"]:
                    return {
                        "url": None,
                        "b64_json": item["b64_json"]
                    }
                elif "url" in item and item["url"]:
                    return {
                        "url": item["url"],
                        "b64_json": None
                    }
            raise ValueError("Resposta do Pollinations.ai não contém dados de imagem válidos.")
        else:
            raise ValueError(f"Pollinations.ai falhou com status {response.status_code}: {response.text}")
    elif model_name.startswith("openrouter/"):
        model_id = model_name.replace("openrouter/", "")
        return _artista_fallback(client, prompt, model_id=model_id)
    else:
        raise ValueError(f"Modelo do Artista desconhecido: {model_name}")

def _artista_fallback(client, prompt: str, model_id: str = "google/gemini-2.5-flash-image") -> dict:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                extra_body={
                    "modalities": ["image"],
                    "image_config": {
                        "aspect_ratio": "9:16"
                    }
                },
                max_tokens=16384,
                timeout=120.0
            )
            choice = response.choices[0]
            message = choice.message
            print("DEBUG ARTISTA FALLBACK MESSAGE:", repr(message))
            
            model_extra = getattr(message, "model_extra", None)
            images_list = None
            if hasattr(message, "images") and message.images:
                images_list = message.images
            elif isinstance(model_extra, dict):
                images_list = model_extra.get("images")
                
            if images_list and len(images_list) > 0:
                img_item = images_list[0]
                url = None
                if isinstance(img_item, dict):
                    url = img_item.get("url")
                    if not url and "image_url" in img_item:
                        img_url_field = img_item["image_url"]
                        if isinstance(img_url_field, dict):
                            url = img_url_field.get("url")
                        else:
                            url = img_url_field
                else:
                    url = getattr(img_item, "url", None)
                    if not url and hasattr(img_item, "image_url"):
                        img_url_field = getattr(img_item, "image_url")
                        if isinstance(img_url_field, dict):
                            url = img_url_field.get("url")
                        elif hasattr(img_url_field, "url"):
                            url = getattr(img_url_field, "url")
                        else:
                            url = img_url_field
                            
                if url and url.startswith("data:image/"):
                    header, base64_data = url.split(";base64,", 1)
                    return {
                        "url": None,
                        "b64_json": base64_data
                    }
                return {
                    "url": url,
                    "b64_json": None
                }
            else:
                if message.content and "data:image" in message.content:
                    import re
                    match = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", message.content)
                    if match:
                        return {
                            "url": None,
                            "b64_json": match.group(1)
                        }
                raise ValueError("Nenhum dado de imagem retornado pela IA de backup do OpenRouter.")
        except Exception as e:
            print(f"Tentativa {attempt + 1} de {max_retries} falhou com erro: {str(e)}")
            if attempt == max_retries - 1:
                raise e
            time.sleep(2.0 * (attempt + 1))


def gerar_imagem_artista(o_client, or_client, g_client, prompt: str, primary_model: str, sse_send: Callable[[str], None], poe_key: str = None, modelos_paths: list = None) -> dict:
    # MODO TESTE: apenas Codex permitido — nenhum outro modelo de imagem será tentado
    models_to_try = ["codex/gpt-image-2"]

    last_error = None
    for idx, model in enumerate(models_to_try):
        sse_send(f"[Artista] Tentando gerar imagem com o modelo: {model} (Tentativa {idx+1}/{len(models_to_try)})...")
        try:
            if model == "openai/gpt-image-2":
                res = _artista_primary(o_client, prompt, model_name=model, poe_key=poe_key, modelos_paths=modelos_paths)
            elif model == "google/gemini-2.5-flash-image" or model == "google/imagen-4.0-generate-001":
                res = _artista_primary(o_client, prompt, g_client=g_client, model_name=model, poe_key=poe_key, modelos_paths=modelos_paths)
            elif model == "pollinations/flux":
                res = _artista_primary(o_client, prompt, model_name=model, poe_key=poe_key, modelos_paths=modelos_paths)
            elif model.startswith("openrouter/"):
                model_id = model.replace("openrouter/", "")
                res = _artista_fallback(or_client, prompt, model_id=model_id)
            else:
                res = _artista_primary(o_client, prompt, model_name=model, poe_key=poe_key, modelos_paths=modelos_paths)
            
            res["model_used"] = model
            sse_send(f"[Artista] Sucesso usando o modelo: {model}!")
            return res
        except Exception as e:
            sse_send(f"[Artista] Falha com o modelo {model}: {str(e)}")
            last_error = e
            
    raise last_error or RuntimeError("Todos os modelos de geração de imagem falharam.")



# --------------------------
# 4. Agente Revisor
# --------------------------

def _revisor_primary(
    client,
    image: Image.Image,
    prompt_designer: str,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    titulo: str = None,
    sse_send: Callable[[str], None] = None
) -> dict:
    prompt_text = (
        f"Você é um revisor de quadrinhos detalhista e rigoroso. Sua tarefa é analisar a imagem de página de quadrinho anexa e verificar se ela atende às exigências do Designer Oriental:\n\n"
        f"Instruções do Designer: {prompt_designer}\n"
        f"Página: {num_pagina} de {total_paginas}\n\n"
        f"Verificações obrigatórias:\n"
        f"1. Estilo Visual: Estilo clássico de pintura em nanquim chinesa (ink wash painting), traços fluidos de pincel, névoa e montanhas taoístas.\n"
        f"2. Caixas de texto/balões de diálogo: Verifique se existem balões de diálogo e caixas de narração adequados na página. Como as IAs de geração costumam criar textos ilegíveis, você deve aprovar a imagem se as caixas de texto/balões de diálogo estiverem presentes e bem dispostas. Não rejeite a imagem por causa de letras borradas ou ilegíveis, desde que os balões e caixas existam e o layout visual represente o roteiro.\n"
    )
    if titulo:
        prompt_text += f"3. Título (Pág 1): Se esta for a página 1, DEVE haver um título destacado no topo da imagem contendo exatamente o texto em português: '{titulo}'. Não aprove se o título estiver incorreto, truncado, ausente ou se for outro título diferente do estabelecido (por exemplo, se o artista desenhar algo diferente de '{titulo}').\n"
    else:
        prompt_text += "3. Título (Pág 1): Se esta for a página 1, DEVE haver um título destacado no topo da imagem.\n"
        
    prompt_text += (
        f"4. Arte Final (Última Pág): Se esta for a página final (página {total_paginas}), a imagem DEVE ser uma arte própria que evidencie de forma poética e espiritual o encerramento do conto.\n"
    )
    if geral:
        prompt_text += f"5. Instruções Gerais de Auditoria do Revisor:\n{geral}\n"
    if especifica:
        prompt_text += f"6. Instrução Específica de Auditoria do Revisor (PRIORIDADE ABSOLUTA - Suplanta as gerais se houver conflito):\n{especifica}\n"
        
    prompt_text += (
        "\nResponda APENAS 'APROVADO' se a página estiver adequada.\n"
        "Caso haja erros graves em relação a estas diretrizes, descreva detalhadamente os erros (em português) em um texto claro para que o artista ajuste o prompt e redesenhe."
    )
    
    def native_fallback():
        if sse_send:
            sse_send("[Revisor] Usando o modelo: Google GenAI (gemini-2.5-flash)")
        contents = [image]
        if arquivo_b64 and arquivo_mime:
            import base64
            part = types.Part.from_bytes(data=base64.b64decode(arquivo_b64), mime_type=arquivo_mime)
            contents.append(part)
        contents.append(prompt_text)
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                max_output_tokens=4096
            )
        )
        content = extract_content_from_gemini(response)
        return {
            "content": content,
            "reasoning": extract_reasoning(response, is_gemini=True)
        }
        
    prompt_with_files = prompt_text
    if arquivo_b64 and arquivo_mime:
        prompt_with_files += _processar_e_descrever_arquivo(client, arquivo_b64, arquivo_mime, "Revisor")
        
    return _executar_agente_texto_visao(
        agent_name="Revisor",
        prompt=prompt_with_files,
        system_instruction="Você é um revisor de quadrinhos detalhista e rigoroso.",
        image=image,
        sse_send=sse_send,
        fallback_or_fn=native_fallback
    )


def _revisor_fallback(
    client,
    image: Image.Image,
    prompt_designer: str,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    g_client = None,
    titulo: str = None,
    sse_send: Callable[[str], None] = None
) -> dict:
    backup_models = [
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4.6"
    ]
    if image is not None:
        backup_models = [m for m in backup_models if m != "deepseek/deepseek-chat"]
    
    if image is not None:
        prompt_text = (
            f"Você é um revisor de quadrinhos detalhista e rigoroso. Sua tarefa é analisar a imagem de página de quadrinho anexa e verificar se ela atende às exigências do Designer Oriental:\n\n"
            f"Instruções do Designer: {prompt_designer}\n"
            f"Página: {num_pagina} de {total_paginas}\n\n"
            f"Verificações obrigatórias:\n"
            f"1. Estilo Visual: Estilo clássico de pintura em nanquim chinesa (ink wash painting), traços fluidos de pincel, névoa e montanhas taoístas.\n"
            f"2. Caixas de texto/balões de diálogo: Verifique se existem balões de diálogo e caixas de narração adequados na página. Como as IAs de geração costumam criar textos ilegíveis, você deve aprovar a imagem se as caixas de texto/balões de diálogo estiverem presentes e bem dispostas. Não rejeite a imagem por causa de letras borradas ou ilegíveis, desde que os balões e caixas existam e o layout visual represente o roteiro.\n"
        )
        if titulo:
            prompt_text += f"3. Título (Pág 1): Se esta for a página 1, DEVE haver um título destacado no topo da imagem contendo exatamente o texto em português: '{titulo}'. Não aprove se o título estiver incorreto, truncado, ausente ou se for outro título diferente do estabelecido (por exemplo, se o artista desenhar algo diferente de '{titulo}').\n"
        else:
            prompt_text += "3. Título (Pág 1): Se esta for a página 1, DEVE haver um título destacado no topo da imagem.\n"
            
        prompt_text += (
            f"4. Arte Final (Última Pág): Se esta for a página final (página {total_paginas}), a imagem DEVE ser uma arte própria que evidencie de forma poética e espiritual o encerramento do conto.\n"
        )
        if geral:
            prompt_text += f"5. Instruções Gerais de Auditoria do Revisor:\n{geral}\n"
        if especifica:
            prompt_text += f"6. Instrução Específica de Auditoria do Revisor (PRIORIDADE ABSOLUTA - Suplanta as gerais se houver conflito):\n{especifica}\n"
            
        prompt_text += (
            "\nResponda APENAS 'APROVADO' se a página estiver adequada.\n"
            "Caso haja erros graves em relação a estas diretrizes, descreva detalhadamente os erros (em português) em um texto claro para que o artista ajuste o prompt e redesenhe."
        )
        
        attachments = []
        if arquivo_b64 and arquivo_mime:
            attachments.append((arquivo_b64, arquivo_mime))
            
        messages = _format_openrouter_payload(
            prompt=prompt_text,
            images=[image],
            attachments=attachments
        )
    else:
        prompt_text = (
            f"Você é um revisor de roteiros de quadrinhos. Como o sistema de visão multimodal falhou, você deve "
            f"revisar a consistência do prompt criado pelo Designer Oriental para a página {num_pagina} de {total_paginas} de um conto taoísta:\n\n"
            f"Prompt do Designer: {prompt_designer}\n"
        )
        if titulo and num_pagina == 1:
            prompt_text += f"\nImportante: Como esta é a página 1, verifique se o prompt do Designer Oriental especifica exatamente o título da história: '{titulo}'.\n"
        if geral:
            prompt_text += f"Instruções Gerais do Revisor: {geral}\n"
        if especifica:
            prompt_text += f"Instrução Específica do Revisor (Prioridade Absoluta): {especifica}\n"
            
        prompt_text += (
            f"\nVerifique se o prompt especifica adequadamente o estilo oriental (ink wash painting) "
            f"e respeita as instruções do usuário. "
            f"Se o prompt estiver adequado, responda APENAS 'APROVADO'. "
            f"Caso falte algo essencial, forneça sugestões de correção para o prompt em português."
        )
        
        attachments = []
        if arquivo_b64 and arquivo_mime:
            attachments.append((arquivo_b64, arquivo_mime))
            
        messages = _format_openrouter_payload(
            prompt=prompt_text,
            system_instruction="Você é um revisor de roteiros. Escreva 'APROVADO' se o prompt estiver adequado, ou sugira modificações.",
            attachments=attachments
        )

    last_error = None
    for model in backup_models:
        try:
            if sse_send:
                sse_send(f"[Revisor] Ativando IA de Backup ({model}) no OpenRouter...")
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=4096
            )
            content = extract_content_from_openai(response)
            if sse_send:
                sse_send(f"[Revisor] Sucesso usando IA de Backup ({model}) no OpenRouter!")
            return {
                "content": content,
                "reasoning": extract_reasoning(response, is_gemini=False)
            }
        except Exception as e:
            last_error = e
            if sse_send:
                sse_send(f"[Revisor] Falha com IA de Backup ({model}): {str(e)}")
                
    raise last_error or RuntimeError("Todos os modelos de backup do Revisor no OpenRouter falharam.")


def _revisor_fallback_claude(
    ant_client,
    image: Image.Image,
    prompt_designer: str,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    titulo: str = None,
    sse_send: Callable[[str], None] = None
) -> dict:
    prompt_text = (
        f"Você é um revisor de quadrinhos detalhista e rigoroso. Sua tarefa é analisar a imagem de página de quadrinho anexa e verificar se ela atende às exigências do Designer Oriental:\n\n"
        f"Instruções do Designer: {prompt_designer}\n"
        f"Página: {num_pagina} de {total_paginas}\n\n"
        f"Verificações obrigatórias:\n"
        f"1. Estilo Visual: Estilo clássico de pintura em nanquim chinesa (ink wash painting), traços fluidos de pincel, névoa e montanhas taoístas.\n"
        f"2. Caixas de texto/balões de diálogo: Verifique se existem balões de diálogo e caixas de narração adequados na página. Como as IAs de geração costumam criar textos ilegíveis, você deve aprovar a imagem se as caixas de texto/balões de diálogo estiverem presentes e bem dispostas. Não rejeite a imagem por causa de letras borradas ou ilegíveis, desde que os balões e caixas existam e o layout visual represente o roteiro.\n"
    )
    if titulo:
        prompt_text += f"3. Título (Pág 1): Se esta for a página 1, DEVE haver um título destacado no topo da imagem contendo exatamente o texto em português: '{titulo}'. Não aprove se o título estiver incorreto, truncado, ausente ou se for outro título diferente do estabelecido (por exemplo, se o artista desenhar algo diferente de '{titulo}').\n"
    else:
        prompt_text += "3. Título (Pág 1): Se esta for a página 1, DEVE haver um título destacado no topo da imagem.\n"
        
    prompt_text += (
        f"4. Arte Final (Última Pág): Se esta for a página final (página {total_paginas}), a imagem DEVE ser uma arte própria que evidencie de forma poética e espiritual o encerramento do conto.\n"
    )
    if geral:
        prompt_text += f"5. Instruções Gerais de Auditoria do Revisor:\n{geral}\n"
    if especifica:
        prompt_text += f"6. Instrução Específica de Auditoria do Revisor (PRIORIDADE ABSOLUTA - Suplanta as gerais se houver conflito):\n{especifica}\n"
        
    prompt_text += (
        "\nResponda APENAS 'APROVADO' se a página estiver adequada.\n"
        "Caso haja erros graves em relação a estas diretrizes, descreva detalhadamente os erros (em português) em um texto claro para que o artista ajuste o prompt e redesenhe."
    )
    
    base64_img = _encode_pil_to_base64(image)
    
    response = ant_client.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64_img,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt_text
                    }
                ],
            }
        ]
    )
    content = response.content[0].text
    return {
        "content": content,
        "reasoning": None
    }


def _roteirista_ponderar_roteiro_primary(client, roteiro: dict, parecer: str) -> dict:
    prompt = (
        f"Roteiro Atual:\n{json.dumps(roteiro, ensure_ascii=False, indent=2)}\n\n"
        f"Parecer do Especialista China:\n{parecer}\n\n"
        "Como Roteirista, analise o parecer do Especialista China. "
        "Você deve ponderar se as alterações sugeridas pioram o seu trabalho (prejudicam a narrativa, a fluidez, a fidelidade ou a coerência do roteiro). "
        "Responda estritamente no formato JSON abaixo, sem markdown, sem explicações extras:\n\n"
        "{\n"
        "  \"sugestoes_existem\": true,\n"
        "  \"piora_o_trabalho\": false,\n"
        "  \"justificativa\": \"Sua justificativa/ponderação...\",\n"
        "  \"decisao\": \"ALTERAR\" ou \"PROSSEGUIR\"\n"
        "}\n\n"
        "Regras para 'decisao':\n"
        "- Se não houver sugestões de alteração no parecer: responda 'PROSSEGUIR' (e sugestoes_existem = false).\n"
        "- Se houver sugestões e elas NÃO pioram o roteiro: responda 'ALTERAR' (deve alterar o roteiro).\n"
        "- Se houver sugestões mas elas PIORAM o roteiro: responda 'PROSSEGUIR' (prosseguir como está)."
    )
    backup_models = [
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4.6"
    ]
    last_error = None
    for model in backup_models:
        try:
            print(f"[Roteirista Ponderador Fallback] Tentando usar IA de Backup ({model}) no OpenRouter...")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Você é o Roteirista ponderando críticas ao seu roteiro."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1024
            )
            content = extract_content_from_openai(response)
            clean_content = clean_json_text(content)
            print(f"[Roteirista Ponderador Fallback] Sucesso usando IA de Backup ({model}) no OpenRouter!")
            return json.loads(clean_content)
        except Exception as e:
            print(f"[Roteirista Ponderador Fallback] Falha com IA de Backup ({model}): {str(e)}")
            last_error = e
    return {"decisao": "PROSSEGUIR"}


def _roteirista_ponderar_roteiro_fallback(client, roteiro: dict, parecer: str) -> dict:
    prompt = (
        f"Roteiro Atual:\n{json.dumps(roteiro, ensure_ascii=False, indent=2)}\n\n"
        f"Parecer do Especialista China:\n{parecer}\n\n"
        "Como Roteirista, analise o parecer do Especialista China. "
        "Você deve ponderar se as alterações sugeridas pioram o seu trabalho (prejudicam a narrativa, a fluidez, a fidelidade ou a coerência do roteiro). "
        "Responda estritamente no formato JSON abaixo, sem markdown, sem explicações extras:\n\n"
        "{\n"
        "  \"sugestoes_existem\": true,\n"
        "  \"piora_o_trabalho\": false,\n"
        "  \"justificativa\": \"Sua justificativa/ponderação...\",\n"
        "  \"decisao\": \"ALTERAR\" ou \"PROSSEGUIR\"\n"
        "}\n\n"
        "Regras para 'decisao':\n"
        "- Se não houver sugestões de alteração no parecer: responda 'PROSSEGUIR' (e sugestoes_existem = false).\n"
        "- Se houver sugestões e elas NÃO pioram o roteiro: responda 'ALTERAR' (deve alterar o roteiro).\n"
        "- Se houver sugestões mas elas PIORAM o roteiro: responda 'PROSSEGUIR' (prosseguir como está)."
    )
    system_instruction = "Você é o Roteirista ponderando críticas ao seu roteiro."
    
    def native_fallback():
        response = client.models.generate_content(
            model=GEMINI_PRO_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                system_instruction=system_instruction
            )
        )
        content = extract_content_from_gemini(response)
        return {
            "content": content,
            "reasoning": extract_reasoning(response, is_gemini=True)
        }
        
    res = _executar_agente_texto_visao(
        agent_name="Roteirista Ponderador Roteiro",
        prompt=prompt,
        system_instruction=system_instruction,
        fallback_or_fn=native_fallback
    )
    try:
        content = res["content"]
        clean_content = clean_json_text(content)
        return json.loads(clean_content)
    except Exception:
        return {"decisao": "PROSSEGUIR"}


def _artista_ponderar_pagina_primary(client, prompt_designer: str, parecer: str) -> dict:
    prompt = (
        f"Prompt do Designer/Artista para a Página:\n{prompt_designer}\n\n"
        f"Parecer do Especialista China:\n{parecer}\n\n"
        "Como Artista/Designer, analise o parecer do Especialista China. "
        "Você deve ponderar se as alterações sugeridas pioram o seu trabalho (prejudicam o visual, a harmonia artística, o estilo nanquim ou a coerência visual). "
        "Responda estritamente no formato JSON abaixo, sem markdown, sem explicações extras:\n\n"
        "{\n"
        "  \"sugestoes_existem\": true,\n"
        "  \"piora_o_trabalho\": false,\n"
        "  \"justificativa\": \"Sua justificativa/ponderação...\",\n"
        "  \"decisao\": \"ALTERAR\" ou \"PROSSEGUIR\"\n"
        "}\n\n"
        "Regras para 'decisao':\n"
        "- Se não houver sugestões de alteração no parecer: responda 'PROSSEGUIR' (e sugestoes_existem = false).\n"
        "- Se houver sugestões e elas NÃO pioram a página: responda 'ALTERAR' (deve redesenhar/alterar).\n"
        "- Se houver sugestões mas elas PIORAM a página: responda 'PROSSEGUIR' (prosseguir como está)."
    )
    system_instruction = "Você é o Artista/Designer ponderando críticas ao seu desenho/prompt. Responda apenas com o JSON solicitado."

    def native_fallback():
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                system_instruction=system_instruction
            )
        )
        content = extract_content_from_gemini(response)
        return {"content": content, "reasoning": None}

    res = _executar_agente_texto_visao(
        agent_name="Designer Ponderador",
        prompt=prompt,
        system_instruction=system_instruction,
        fallback_or_fn=native_fallback
    )
    try:
        clean_content = clean_json_text(res["content"])
        return json.loads(clean_content)
    except Exception:
        return {"decisao": "PROSSEGUIR"}


def _artista_ponderar_pagina_fallback(client, prompt_designer: str, parecer: str) -> dict:
    prompt = (
        f"Prompt do Designer/Artista para a Página:\n{prompt_designer}\n\n"
        f"Parecer do Especialista China:\n{parecer}\n\n"
        "Como Artista/Designer, analise o parecer do Especialista China. "
        "Você deve ponderar se as alterações sugeridas pioram o seu trabalho (prejudicam o visual, a harmonia artística, o estilo nanquim ou a coerência visual). "
        "Responda estritamente no formato JSON abaixo, sem markdown, sem explicações extras:\n\n"
        "{\n"
        "  \"sugestoes_existem\": true,\n"
        "  \"piora_o_trabalho\": false,\n"
        "  \"justificativa\": \"Sua justificativa/ponderação...\",\n"
        "  \"decisao\": \"ALTERAR\" ou \"PROSSEGUIR\"\n"
        "}\n\n"
        "Regras para 'decisao':\n"
        "- Se não houver sugestões de alteração no parecer: responda 'PROSSEGUIR' (e sugestoes_existem = false).\n"
        "- Se houver sugestões e elas NÃO pioram a página: responda 'ALTERAR' (deve redesenhar/alterar).\n"
        "- Se houver sugestões mas elas PIORAM a página: responda 'PROSSEGUIR' (prosseguir como está)."
    )
    backup_models = [
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4.6"
    ]
    last_error = None
    for model in backup_models:
        try:
            print(f"[Artista Ponderador Fallback] Tentando usar IA de Backup ({model}) no OpenRouter...")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Você é o Artista/Designer ponderando críticas ao seu desenho/prompt."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1024
            )
            content = extract_content_from_openai(response)
            clean_content = clean_json_text(content)
            print(f"[Artista Ponderador Fallback] Sucesso usando IA de Backup ({model}) no OpenRouter!")
            return json.loads(clean_content)
        except Exception as e:
            print(f"[Artista Ponderador Fallback] Falha com IA de Backup ({model}): {str(e)}")
            last_error = e
    return {"decisao": "PROSSEGUIR"}


# --------------------------
# 5. Agente Especialista China
# --------------------------

def _especialista_roteiro_primary(
    client,
    conto: str,
    roteiro: dict,
    geral: str = None,
    especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None
) -> dict:
    roteiro_str = json.dumps(roteiro, ensure_ascii=False, indent=2)
    prompt_text = (
        f"Conto Taoísta Original:\n{conto}\n\n"
        f"Roteiro de HQ Estruturado:\n{roteiro_str}\n\n"
        f"Instruções:\n"
        f"1. Analise se a tradução e adaptação do conto mantêm a essência filosófica do autor original ou do taoísmo clássico.\n"
        f"2. Avalie o roteiro da HQ (título, narrativas, falas, número de páginas e de quadrinhos).\n"
        f"3. Responda seguindo estritamente a estrutura do seu formato de revisão:\n"
        f"## RESULTADO GERAL\n"
        f"### [Resultado]\n\n"
        f"## ANÁLISE\n"
        f"[Sua análise filosófica]\n\n"
        f"## PROBLEMAS ENCONTRADOS\n"
        f"[Lista de problemas ou 'Nenhum' se aprovado]\n\n"
        f"## SUGESTÕES DE MELHORIA\n"
        f"[Lista de sugestões ou 'Nenhuma']\n\n"
        f"## DECISÃO FINAL\n"
        f"[Escolha uma opção de decisão final]"
    )
    
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"
        
    system_instruction = "Você é o Especialista China. Analise o conto e o roteiro quanto à essência taoísta e traduções."
    
    def native_fallback():
        contents = []
        if arquivo_b64 and arquivo_mime:
            import base64
            part = types.Part.from_bytes(data=base64.b64decode(arquivo_b64), mime_type=arquivo_mime)
            contents.append(part)
        contents.append(prompt_text)
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                max_output_tokens=4096,
                system_instruction=system_instruction
            )
        )
        content = extract_content_from_gemini(response)
        return {
            "content": content,
            "reasoning": extract_reasoning(response, is_gemini=True)
        }
        
    prompt_with_files = prompt_text
    if arquivo_b64 and arquivo_mime:
        prompt_with_files += _processar_e_descrever_arquivo(client, arquivo_b64, arquivo_mime, "Especialista China Roteiro")
        
    return _executar_agente_texto_visao(
        agent_name="Especialista China Roteiro",
        prompt=prompt_with_files,
        system_instruction=system_instruction,
        fallback_or_fn=native_fallback
    )

def _especialista_roteiro_fallback(
    client,
    conto: str,
    roteiro: dict,
    geral: str = None,
    especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    g_client = None
) -> dict:
    roteiro_str = json.dumps(roteiro, ensure_ascii=False, indent=2)
    prompt_text = (
        f"Conto Taoísta Original:\n{conto}\n\n"
        f"Roteiro de HQ Estruturado:\n{roteiro_str}\n\n"
        f"Instruções:\n"
        f"1. Analise se a tradução e adaptação do conto mantêm a essência filosófica do autor original ou do taoísmo clássico.\n"
        f"2. Avalie o roteiro da HQ (título, narrativas, falas, número de páginas e de quadrinhos).\n"
        f"3. Responda seguindo estritamente a estrutura do seu formato de revisão."
    )
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"
        
    attachments = []
    if arquivo_b64 and arquivo_mime:
        attachments.append((arquivo_b64, arquivo_mime))
        
    messages = _format_openrouter_payload(
        prompt=prompt_text,
        system_instruction="Você é o Especialista China. Analise o conto e o roteiro quanto à essência taoísta e traduções.",
        attachments=attachments
    )

    backup_models = [
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4.6"
    ]
    last_error = None
    for model in backup_models:
        try:
            print(f"[Especialista China Roteiro Fallback] Tentando usar IA de Backup ({model}) no OpenRouter...")
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=4096
            )
            content = extract_content_from_openai(response)
            print(f"[Especialista China Roteiro Fallback] Sucesso usando IA de Backup ({model}) no OpenRouter!")
            return {
                "content": content,
                "reasoning": extract_reasoning(response, is_gemini=False)
            }
        except Exception as e:
            print(f"[Especialista China Roteiro Fallback] Falha com IA de Backup ({model}): {str(e)}")
            last_error = e
    raise last_error or RuntimeError("Todos os modelos de backup do Especialista China Roteiro no OpenRouter falharam.")

def _especialista_pagina_primary(
    client,
    image: Image.Image,
    prompt_designer: str,
    pagina_script: dict,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    titulo: str = None
) -> dict:
    prompt_text = (
        f"Analise a imagem desta página de quadrinho taoísta (página {num_pagina} de {total_paginas}).\n\n"
        f"ESCOPO EXCLUSIVO DA SUA ANÁLISE — analise APENAS o que está visível na imagem:\n"
        f"1. Leitura taoísta da arte: os elementos visuais (natureza, névoa, luz, símbolos, gestos) evocam corretamente a filosofia taoísta?\n"
        f"2. Caracteres e ideogramas chineses visíveis na arte: estão graficamente corretos, legíveis e conceitualmente adequados?\n"
        f"3. Qualidade filosófica dos diálogos visíveis: as falas e narrações refletem adequadamente os princípios taoístas do autor da obra?\n\n"
        f"NÃO compare a imagem com nenhum roteiro, script ou prompt. NÃO verifique se a arte corresponde ao que foi planejado. "
        f"Analise SOMENTE o que está desenhado na imagem.\n"
    )
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"

    system_instruction = "Você é o Especialista China analisando arte de quadrinhos taoísta. Foque exclusivamente no que está visível na imagem — elementos taoístas, caracteres chineses e qualidade filosófica dos diálogos. Nunca compare com roteiro ou script."
    
    def native_fallback():
        contents = [image]
        if arquivo_b64 and arquivo_mime:
            import base64
            part = types.Part.from_bytes(data=base64.b64decode(arquivo_b64), mime_type=arquivo_mime)
            contents.append(part)
        contents.append(prompt_text)
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                max_output_tokens=4096,
                system_instruction=system_instruction
            )
        )
        content = extract_content_from_gemini(response)
        return {
            "content": content,
            "reasoning": extract_reasoning(response, is_gemini=True)
        }
        
    prompt_with_files = prompt_text
    if arquivo_b64 and arquivo_mime:
        prompt_with_files += _processar_e_descrever_arquivo(client, arquivo_b64, arquivo_mime, "Especialista China Página")
        
    return _executar_agente_texto_visao(
        agent_name="Especialista China Página",
        prompt=prompt_with_files,
        system_instruction=system_instruction,
        image=image,
        fallback_or_fn=native_fallback
    )

def _especialista_pagina_fallback(
    client,
    image: Image.Image,
    prompt_designer: str,
    pagina_script: dict,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    g_client = None,
    titulo: str = None
) -> dict:
    prompt_text = (
        f"Analise a imagem desta página de quadrinho taoísta (página {num_pagina} de {total_paginas}).\n\n"
        f"ESCOPO EXCLUSIVO DA SUA ANÁLISE — analise APENAS o que está visível na imagem:\n"
        f"1. Leitura taoísta da arte: os elementos visuais (natureza, névoa, luz, símbolos, gestos) evocam corretamente a filosofia taoísta?\n"
        f"2. Caracteres e ideogramas chineses visíveis na arte: estão graficamente corretos, legíveis e conceitualmente adequados?\n"
        f"3. Qualidade filosófica dos diálogos visíveis: as falas e narrações refletem adequadamente os princípios taoístas do autor da obra?\n\n"
        f"NÃO compare a imagem com nenhum roteiro, script ou prompt. NÃO verifique se a arte corresponde ao que foi planejado. "
        f"Analise SOMENTE o que está desenhado na imagem.\n"
    )
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"
    attachments = []
    if arquivo_b64 and arquivo_mime:
        attachments.append((arquivo_b64, arquivo_mime))

    messages = _format_openrouter_payload(
        prompt=prompt_text,
        system_instruction="Você é o Especialista China analisando arte de quadrinhos taoísta. Foque exclusivamente no que está visível na imagem — elementos taoístas, caracteres chineses e qualidade filosófica dos diálogos. Nunca compare com roteiro ou script.",
        images=[image] if image else None,
        attachments=attachments
    )

    backup_models = [
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4.6"
    ]
    if image is not None:
        backup_models = [m for m in backup_models if m != "deepseek/deepseek-chat"]
        
    last_error = None
    for model in backup_models:
        try:
            print(f"[Especialista China Pagina Fallback] Tentando usar IA de Backup ({model}) no OpenRouter...")
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=4096
            )
            content = extract_content_from_openai(response)
            print(f"[Especialista China Pagina Fallback] Sucesso usando IA de Backup ({model}) no OpenRouter!")
            return {
                "content": content,
                "reasoning": extract_reasoning(response, is_gemini=False)
            }
        except Exception as e:
            print(f"[Especialista China Pagina Fallback] Falha com IA de Backup ({model}): {str(e)}")
            last_error = e
    raise last_error or RuntimeError("Todos os modelos de backup do Especialista China Pagina no OpenRouter falharam.")


def _especialista_roteiro_fallback_claude(
    ant_client,
    conto: str,
    roteiro: dict,
    geral: str = None,
    especifica: str = None
) -> dict:
    roteiro_str = json.dumps(roteiro, ensure_ascii=False, indent=2)
    prompt_text = (
        f"Conto Taoísta Original:\n{conto}\n\n"
        f"Roteiro de HQ Estruturado:\n{roteiro_str}\n\n"
        f"Instruções:\n"
        f"1. Analise se a tradução e adaptação do conto mantêm a essência filosófica do autor original ou do taoísmo clássico.\n"
        f"2. Avalie o roteiro da HQ (título, narrativas, falas, número de páginas e de quadrinhos).\n"
        f"3. Responda seguindo estritamente a estrutura do seu formato de revisão:\n"
        f"## RESULTADO GERAL\n"
        f"### [Resultado]\n\n"
        f"## ANÁLISE\n"
        f"[Sua análise filosófica]\n\n"
        f"## PROBLEMAS ENCONTRADOS\n"
        f"[Lista de problemas ou 'Nenhum' se aprovado]\n\n"
        f"## SUGESTÕES DE MELHORIA\n"
        f"[Lista de sugestões ou 'Nenhuma']\n\n"
        f"## DECISÃO FINAL\n"
        f"[Escolha uma opção de decisão final]"
    )
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"
        
    response = ant_client.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=1024,
        messages=[
            {"role": "user", "content": prompt_text}
        ]
    )
    content = response.content[0].text
    return {
        "content": content,
        "reasoning": None
    }


def _especialista_prompt_primary(
    client,
    prompt_designer: str,
    pagina_script: dict,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    titulo: str = None
) -> dict:
    script_str = json.dumps(pagina_script, ensure_ascii=False, indent=2)
    prompt_text = (
        f"Você é o Especialista China. Sua tarefa é analisar o prompt visual proposto pelo Designer Oriental para a página {num_pagina} de {total_paginas} de um conto taoísta (Título: {titulo}) com base no roteiro daquela página.\n\n"
        f"Roteiro da Página:\n{script_str}\n\n"
        f"Prompt Proposto pelo Designer Oriental:\n{prompt_designer}\n\n"
        f"Instruções:\n"
        f"1. Verifique se o prompt proposto respeita o roteiro, a essência filosófica e a consistência cultural/linguística.\n"
        f"2. Sugira ajustes ou melhorias se houver termos incorretos, inconsistências ou se puder melhorar a ambientação taoísta.\n"
        f"3. Responda seguindo estritamente a estrutura do seu formato de revisão:\n"
        f"## RESULTADO GERAL\n"
        f"### [Resultado (APROVADO / APROVADO COM RESSALVAS / REPROVADO)]\n\n"
        f"## ANÁLISE\n"
        f"[Sua análise em português]\n\n"
        f"## PROBLEMAS ENCONTRADOS\n"
        f"[Lista de problemas ou 'Nenhum']\n\n"
        f"## SUGESTÕES DE MELHORIA\n"
        f"[Lista de sugestões de melhoria (escreva no formato de prompt em inglês ou português para a IA do Designer)]\n\n"
        f"## DECISÃO FINAL\n"
        f"[AUTORIZADO PARA PRODUÇÃO / AUTORIZADO COM AJUSTES / NÃO AUTORIZADO]"
    )
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"
        
    system_instruction = "Você é o Especialista China. Revise o prompt do Designer de acordo com a filosofia taoísta e a consistência cultural."
    
    def native_fallback():
        contents = []
        if arquivo_b64 and arquivo_mime:
            import base64
            part = types.Part.from_bytes(data=base64.b64decode(arquivo_b64), mime_type=arquivo_mime)
            contents.append(part)
        contents.append(prompt_text)
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction
            )
        )
        content = extract_content_from_gemini(response)
        return {
            "content": content,
            "reasoning": extract_reasoning(response, is_gemini=True)
        }
        
    prompt_with_files = prompt_text
    if arquivo_b64 and arquivo_mime:
        prompt_with_files += _processar_e_descrever_arquivo(client, arquivo_b64, arquivo_mime, "Especialista China Prompt")
        
    return _executar_agente_texto_visao(
        agent_name="Especialista China Prompt",
        prompt=prompt_with_files,
        system_instruction=system_instruction,
        fallback_or_fn=native_fallback
    )


def _especialista_prompt_fallback(
    client,
    prompt_designer: str,
    pagina_script: dict,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    arquivo_b64: str = None,
    arquivo_mime: str = None,
    titulo: str = None
) -> dict:
    script_str = json.dumps(pagina_script, ensure_ascii=False, indent=2)
    prompt_text = (
        f"Você é o Especialista China. Sua tarefa é analisar o prompt visual proposto pelo Designer Oriental para a página {num_pagina} de {total_paginas} de um conto taoísta (Título: {titulo}) com base no roteiro daquela página.\n\n"
        f"Roteiro da Página:\n{script_str}\n\n"
        f"Prompt Proposto pelo Designer Oriental:\n{prompt_designer}\n\n"
        f"Instruções:\n"
        f"1. Verifique se o prompt proposto respeita o roteiro, a essência filosófica e a consistência cultural/linguística.\n"
        f"2. Sugira ajustes ou melhorias se houver termos incorretos, inconsistências ou se puder melhorar a ambientação taoísta.\n"
        f"3. Responda seguindo estritamente a estrutura do seu formato de revisão:\n"
        f"## RESULTADO GERAL\n"
        f"### [Resultado (APROVADO / APROVADO COM RESSALVAS / REPROVADO)]\n\n"
        f"## ANÁLISE\n"
        f"[Sua análise em português]\n\n"
        f"## PROBLEMAS ENCONTRADOS\n"
        f"[Lista de problemas ou 'Nenhum']\n\n"
        f"## SUGESTÕES DE MELHORIA\n"
        f"[Lista de sugestões de melhoria (escreva no formato de prompt em inglês ou português para a IA do Designer)]\n\n"
        f"## DECISÃO FINAL\n"
        f"[AUTORIZADO PARA PRODUÇÃO / AUTORIZADO COM AJUSTES / NÃO AUTORIZADO]"
    )
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"
        
    backup_models = [
        "google/gemini-2.5-flash",
        "deepseek/deepseek-chat",
        "google/gemini-2.5-pro",
        "openai/gpt-4o",
        "anthropic/claude-sonnet-4.6"
    ]
    last_error = None
    for model in backup_models:
        try:
            print(f"[Especialista China Prompt Fallback] Tentando usar IA de Backup ({model}) no OpenRouter...")
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Você é o Especialista China. Revise o prompt do Designer de acordo com a filosofia taoísta e a consistência cultural."},
                    {"role": "user", "content": prompt_text}
                ]
            )
            content = extract_content_from_openai(response)
            print(f"[Especialista China Prompt Fallback] Sucesso usando IA de Backup ({model}) no OpenRouter!")
            return {
                "content": content,
                "reasoning": None
            }
        except Exception as e:
            print(f"[Especialista China Prompt Fallback] Falha com IA de Backup ({model}): {str(e)}")
            last_error = e
    raise last_error or RuntimeError("Todos os modelos de backup do Especialista China Prompt no OpenRouter falharam.")


def _especialista_prompt_fallback_claude(
    ant_client,
    prompt_designer: str,
    pagina_script: dict,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    titulo: str = None
) -> dict:
    script_str = json.dumps(pagina_script, ensure_ascii=False, indent=2)
    prompt_text = (
        f"Você é o Especialista China. Sua tarefa é analisar o prompt visual proposto pelo Designer Oriental para a página {num_pagina} de {total_paginas} de um conto taoísta (Título: {titulo}) com base no roteiro daquela página.\n\n"
        f"Roteiro da Página:\n{script_str}\n\n"
        f"Prompt Proposto pelo Designer Oriental:\n{prompt_designer}\n\n"
        f"Instruções:\n"
        f"1. Verifique se o prompt proposto respeita o roteiro, a essência filosófica e a consistência cultural/linguística.\n"
        f"2. Sugira ajustes ou melhorias se houver termos incorretos, inconsistências ou se puder melhorar a ambientação taoísta.\n"
        f"3. Responda seguindo estritamente a estrutura do seu formato de revisão:\n"
        f"## RESULTADO GERAL\n"
        f"### [Resultado (APROVADO / APROVADO COM RESSALVAS / REPROVADO)]\n\n"
        f"## ANÁLISE\n"
        f"[Sua análise em português]\n\n"
        f"## PROBLEMAS ENCONTRADOS\n"
        f"[Lista de problemas ou 'Nenhum']\n\n"
        f"## SUGESTÕES DE MELHORIA\n"
        f"[Lista de sugestões de melhoria (escreva no formato de prompt em inglês ou português para a IA do Designer)]\n\n"
        f"## DECISÃO FINAL\n"
        f"[AUTORIZADO PARA PRODUÇÃO / AUTORIZADO COM AJUSTES / NÃO AUTORIZADO]"
    )
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"
        
    response = ant_client.messages.create(
        model="claude-3-5-sonnet-20241022",
        max_tokens=2000,
        messages=[
            {"role": "user", "content": prompt_text}
        ]
    )
    content = response.content[0].text
    return {
        "content": content,
        "reasoning": None
    }




def _especialista_pagina_fallback_claude(
    ant_client,
    image: Image.Image,
    prompt_designer: str,
    pagina_script: dict,
    num_pagina: int,
    total_paginas: int,
    geral: str = None,
    especifica: str = None,
    titulo: str = None
) -> dict:
    prompt_text = (
        f"Analise a imagem desta página de quadrinho taoísta (página {num_pagina} de {total_paginas}).\n\n"
        f"ESCOPO EXCLUSIVO DA SUA ANÁLISE — analise APENAS o que está visível na imagem:\n"
        f"1. Leitura taoísta da arte: os elementos visuais (natureza, névoa, luz, símbolos, gestos) evocam corretamente a filosofia taoísta?\n"
        f"2. Caracteres e ideogramas chineses visíveis na arte: estão graficamente corretos, legíveis e conceitualmente adequados?\n"
        f"3. Qualidade filosófica dos diálogos visíveis: as falas e narrações refletem adequadamente os princípios taoístas do autor da obra?\n\n"
        f"NÃO compare a imagem com nenhum roteiro, script ou prompt. NÃO verifique se a arte corresponde ao que foi planejado. "
        f"Analise SOMENTE o que está desenhado na imagem.\n"
    )
    if geral:
        prompt_text += f"\n\nDiretrizes Gerais do Especialista:\n{geral}\n"
    if especifica:
        prompt_text += f"\n\nInstrução Específica (Prioridade Máxima):\n{especifica}\n"
        
    base64_img = _encode_pil_to_base64(image)
    
    response = ant_client.messages.create(
        model="claude-3-5-sonnet-latest",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64_img,
                        },
                    },
                    {
                        "type": "text",
                        "text": prompt_text
                    }
                ],
            }
        ]
    )
    content = response.content[0].text
    return {
        "content": content,
        "reasoning": None
    }


def _carregar_modelos() -> list:
    """
    Carrega todas as imagens da pasta app/static/modelos/ como PIL Images.
    Retorna uma lista de (PIL.Image, caminho_absoluto).
    """
    modelos_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "modelos")
    os.makedirs(modelos_dir, exist_ok=True)
    extensoes = (".png", ".jpg", ".jpeg", ".webp")
    modelos = []
    for fname in sorted(os.listdir(modelos_dir)):
        if fname.lower().endswith(extensoes):
            fpath = os.path.join(modelos_dir, fname)
            try:
                img = Image.open(fpath).convert("RGB")
                modelos.append((img, fpath))
            except Exception as e:
                print(f"[Modelos] Erro ao carregar '{fpath}': {e}")
    return modelos


def load_instruction_file(agent_name: str) -> str:
    instructions_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instructions")
    file_path = os.path.join(instructions_dir, f"{agent_name}_geral.md")
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""
    return ""


# --------------------------
# Orquestrador Principal
# --------------------------

def processar_conto_taoista(
    conto: str,
    output_dir: str,
    sse_send: Callable[[str], None],
    custom_keys: dict = None,
    ref_image: Image.Image = None,
    instrucoes: dict = None,
    wait_for_user_decision: Callable[[int, str], str] = None,
    check_status: Callable[[], None] = None,
    artista_model: str = "codex/gpt-image-2"
) -> List[str]:
    """
    Orquestra todo o pipeline de geração usando os agentes de IA.
    Permite chaves de API dinâmicas.
    """
    keys = custom_keys or {}
    inst = instrucoes or {}
    
    # Extração de instruções estruturadas por agente e seus anexos de arquivos
    rot_cfg = inst.get("roteirista", {})
    rot_geral = rot_cfg.get("geral") or load_instruction_file("roteirista")
    rot_especifica = rot_cfg.get("especifica")
    rot_arquivo_b64 = rot_cfg.get("arquivo_b64")
    rot_arquivo_mime = rot_cfg.get("arquivo_mime")
    
    des_cfg = inst.get("designer", {})
    des_geral = des_cfg.get("geral") or load_instruction_file("designer")
    des_especifica = des_cfg.get("especifica")
    des_arquivo_b64 = des_cfg.get("arquivo_b64")
    des_arquivo_mime = des_cfg.get("arquivo_mime")
    
    art_cfg = inst.get("artista", {})
    art_geral = art_cfg.get("geral") or load_instruction_file("artista")
    art_especifica = art_cfg.get("especifica")
    art_arquivo_b64 = art_cfg.get("arquivo_b64")
    art_arquivo_mime = art_cfg.get("arquivo_mime")
    
    rev_cfg = inst.get("revisor", {})
    rev_geral = rev_cfg.get("geral") or load_instruction_file("revisor")
    rev_especifica = rev_cfg.get("especifica")
    rev_arquivo_b64 = rev_cfg.get("arquivo_b64")
    rev_arquivo_mime = rev_cfg.get("arquivo_mime")
    
    esp_cfg = inst.get("especialista", {})
    esp_geral = esp_cfg.get("geral") or load_instruction_file("especialista")
    esp_especifica = esp_cfg.get("especifica")
    esp_arquivo_b64 = esp_cfg.get("arquivo_b64")
    esp_arquivo_mime = esp_cfg.get("arquivo_mime")
    
    # Inicializa clientes com chaves fornecidas ou do .env
    g_key = keys.get("gemini_api_key") or os.getenv("GEMINI_API_KEY")
    o_key = keys.get("openai_api_key") or os.getenv("OPENAI_API_KEY")
    or_key = keys.get("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY")
    poe_key = keys.get("poe_api_key") or os.getenv("POE_API_KEY")
    ant_key = keys.get("anthropic_api_key") or keys.get("claude_api_key") or os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    
    if not g_key:
        raise ValueError("Chave de API do Gemini não disponível.")
    if not o_key:
        raise ValueError("Chave de API da OpenAI não disponível.")
    if not or_key:
        raise ValueError("Chave de API do OpenRouter não disponível.")
        
    g_client = genai.Client(api_key=g_key)
    o_client = openai.OpenAI(api_key=o_key, timeout=120.0)
    or_client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=or_key, timeout=120.0)
    
    ant_client = None
    if ant_key:
        try:
            ant_client = anthropic.Anthropic(api_key=ant_key)
        except Exception as e:
            sse_send(f"[Sistema] Aviso: Falha ao inicializar o cliente Anthropic: {str(e)}")
    
    os.makedirs(output_dir, exist_ok=True)
    sse_send("[Sistema] Inicializando clientes de API...")

    # Carrega imagens de referência visual da pasta Modelos
    modelos_raw = _carregar_modelos()
    modelos_images = [img for img, _ in modelos_raw]   # PIL Images — para agentes de visão
    modelos_paths  = [path for _, path in modelos_raw]  # Caminhos — para o Codex
    if modelos_images:
        sse_send(f"[Sistema] {len(modelos_images)} imagem(ns) de modelo carregada(s) da pasta Modelos.")
    else:
        sse_send("[Sistema] Nenhuma imagem encontrada na pasta Modelos. O estilo será guiado apenas pelo prompt.")
    # Usa o primeiro modelo como ref_image principal (se o usuário não enviou uma)
    if not ref_image and modelos_images:
        ref_image = modelos_images[0]
    
    # Gerar pasta do conto baseada em hash MD5 do texto
    import hashlib
    import re
    limpo = re.sub(r'[^\w\s]', '', conto).lower()
    palavras = [w for w in limpo.split() if len(w) > 2][:4]
    prefix = "_".join(palavras) if palavras else "conto"
    h = hashlib.md5(conto.encode('utf-8')).hexdigest()[:8]
    tale_folder_name = f"{prefix}_{h}"
    tale_dir = os.path.join(output_dir, tale_folder_name)
    os.makedirs(tale_dir, exist_ok=True)
    
    # Subpastas dedicadas conforme novas diretrizes do usuário
    revisor_dir = os.path.join(tale_dir, "revisor")
    especialista_dir = os.path.join(tale_dir, "especialista")
    artista_dir = os.path.join(tale_dir, "artista")
    aprovadas_dir = os.path.join(artista_dir, "aprovadas")
    rejeitadas_dir = os.path.join(artista_dir, "rejeitadas")
    
    os.makedirs(revisor_dir, exist_ok=True)
    os.makedirs(especialista_dir, exist_ok=True)
    os.makedirs(artista_dir, exist_ok=True)
    os.makedirs(aprovadas_dir, exist_ok=True)
    os.makedirs(rejeitadas_dir, exist_ok=True)
    
    # Retrocompatibilidade: Migrar arquivos antigos da raiz do conto para as novas pastas
    try:
        import shutil
        for f_name in os.listdir(tale_dir):
            src_path = os.path.join(tale_dir, f_name)
            if os.path.isfile(src_path):
                if f_name.startswith("parecer_revisor"):
                    shutil.move(src_path, os.path.join(revisor_dir, f_name))
                elif f_name.startswith("parecer_especialista"):
                    shutil.move(src_path, os.path.join(especialista_dir, f_name))
                elif f_name.startswith("pagina_") and f_name.endswith(".png"):
                    shutil.move(src_path, os.path.join(aprovadas_dir, f_name))
                    
        old_rejeitadas = os.path.join(tale_dir, "rejeitadas")
        if os.path.isdir(old_rejeitadas):
            for f_name in os.listdir(old_rejeitadas):
                src_f = os.path.join(old_rejeitadas, f_name)
                if os.path.isfile(src_f):
                    shutil.move(src_f, os.path.join(rejeitadas_dir, f_name))
            try:
                os.rmdir(old_rejeitadas)
            except Exception:
                pass
    except Exception as e_migra:
        print(f"Erro na migração de retrocompatibilidade: {e_migra}")
    
    # Caminho do roteiro na pasta
    roteiro_path = os.path.join(tale_dir, "roteiro.json")
    roteiro = None
    roteiro_cacheado = False
    
    if os.path.exists(roteiro_path):
        try:
            sse_send("[Sistema] Roteiro encontrado localmente na pasta do conto! Carregando do cache...")
            with open(roteiro_path, "r", encoding="utf-8") as f:
                roteiro = json.load(f)
            # Corrige eventual formato incorreto salvo em execuções anteriores
            if isinstance(roteiro, dict) and "content" in roteiro and "paginas" not in roteiro:
                sse_send("[Sistema] Cache do roteiro corrompido detectado. Recuperando...")
                raw = roteiro["content"]
                cleaned = clean_json_text(raw)
                roteiro = json.loads(cleaned)
                with open(roteiro_path, "w", encoding="utf-8") as f:
                    json.dump(roteiro, f, ensure_ascii=False, indent=2)
            # Valida se o roteiro tem páginas — cache inválido deve ser descartado
            paginas_cache = roteiro.get("paginas", []) if isinstance(roteiro, dict) else []
            if not paginas_cache:
                sse_send("[Sistema] Cache do roteiro inválido (0 páginas). Descartando e gerando novo roteiro...")
                roteiro = None
            else:
                roteiro_cacheado = True
                sse_send("[Roteirista] Roteiro carregado com sucesso do cache local.")
        except Exception as e:
            sse_send(f"[Sistema] Erro ao carregar roteiro cacheado: {str(e)}. Gerando novo roteiro...")
            roteiro = None
            
    if not roteiro:
        # 1. Roteirização (Roteirista)
        sse_send("[Roteirista] Lendo conto taoísta e criando o roteiro da HQ...")
        raw_roteiro_res = run_with_retry(
            "Roteirista",
            lambda: _roteirista_fallback(g_client, conto, rot_geral, rot_especifica, rot_arquivo_b64, rot_arquivo_mime, sse_send=sse_send),
            lambda: _roteirista_primary(or_client, conto, rot_geral, rot_especifica, rot_arquivo_b64, rot_arquivo_mime, g_client, sse_send=sse_send),
            sse_send
        )
        raw_roteiro = raw_roteiro_res["content"]
        reasoning = raw_roteiro_res.get("reasoning")
        cleaned = clean_json_text(raw_roteiro)
        roteiro = json.loads(cleaned)
        
        # Salva o roteiro na pasta do conto
        with open(roteiro_path, "w", encoding="utf-8") as f:
            json.dump(roteiro, f, ensure_ascii=False, indent=2)
        _drive_upload(roteiro_path)
        sse_send(f"[Sistema] Novo roteiro salvo na pasta do conto: {roteiro_path}")
        
        if reasoning:
            sse_send(f"[Roteirista] JSON:{json.dumps({'text': 'Roteiro de HQ estruturado criado!', 'reasoning': reasoning}, ensure_ascii=False)}")
        else:
            sse_send("[Roteirista] Roteiro de HQ estruturado criado!")
    
    titulo = roteiro.get("titulo", "Conto Taoista")
    paginas = roteiro.get("paginas", [])
    total_paginas = roteiro.get("total_paginas") or len(paginas)
    
    sse_send(f"[Sistema] Roteiro pronto: '{titulo}' | Total de páginas: {total_paginas}")
    
    # 1.5. Análise do Conto e Roteiro pelo Especialista China (máximo 2 vezes se reprovado)
    if not roteiro_cacheado:
        tentativas_esp_roteiro = 0
        max_tentativas_esp_roteiro = 2
        roteiro_finalizado = False
        
        while not roteiro_finalizado and tentativas_esp_roteiro < max_tentativas_esp_roteiro:
            if check_status:
                check_status()
            # Re-avalia as instruções do Especialista China caso tenham sido atualizadas mid-run
            esp_especifica = inst.get("especialista", {}).get("especifica")
            esp_arquivo_b64 = inst.get("especialista", {}).get("arquivo_b64")
            esp_arquivo_mime = inst.get("especialista", {}).get("arquivo_mime")
            
            tentativas_esp_roteiro += 1
            sse_send(f"[Especialista China] Analisando tradução do conto e estrutura do roteiro (Revisão {tentativas_esp_roteiro}/{max_tentativas_esp_roteiro})...")
            
            def especialista_roteiro_fallback_call():
                try:
                    sse_send("[Especialista China] Acionando backups no OpenRouter...")
                    return _especialista_roteiro_fallback(or_client, conto, roteiro, esp_geral, esp_especifica, esp_arquivo_b64, esp_arquivo_mime)
                except Exception as e_or:
                    if ant_client:
                        sse_send(f"[Especialista China] Backups do OpenRouter falharam: {str(e_or)}. Acionando Claude 3.5 Sonnet como último recurso...")
                        try:
                            return _especialista_roteiro_fallback_claude(ant_client, conto, roteiro, esp_geral, esp_especifica)
                        except Exception as e_claude:
                            sse_send(f"[Especialista China] Claude de Backup também falhou: {str(e_claude)}.")
                            raise e_claude
                    raise e_or
                
            esp_roteiro_res = run_with_retry(
                "Especialista China",
                lambda: _especialista_roteiro_primary(g_client, conto, roteiro, esp_geral, esp_especifica, esp_arquivo_b64, esp_arquivo_mime),
                especialista_roteiro_fallback_call,
                sse_send
            )
            esp_roteiro_content = esp_roteiro_res["content"]
            esp_roteiro_reasoning = esp_roteiro_res.get("reasoning")
            
            # Salva o parecer com o número da tentativa e status na pasta do conto
            _status_rot = _status_parecer(esp_roteiro_content)
            parecer_roteiro_path = os.path.join(especialista_dir, f"parecer_especialista_roteiro_{tentativas_esp_roteiro}_{_status_rot}.txt")
            with open(parecer_roteiro_path, "w", encoding="utf-8") as f:
                f.write(esp_roteiro_content)
            _drive_upload(parecer_roteiro_path)
            sse_send(f"[Sistema] Parecer {tentativas_esp_roteiro} do Especialista China para o roteiro salvo em: {parecer_roteiro_path}")

            # Salva também no arquivo principal para visualização/compatibilidade
            parecer_principal_path = os.path.join(especialista_dir, "parecer_especialista_roteiro.txt")
            with open(parecer_principal_path, "w", encoding="utf-8") as f:
                f.write(esp_roteiro_content)
            _drive_upload(parecer_principal_path)
            
            if esp_roteiro_reasoning:
                sse_send(f"[Especialista China] JSON:{json.dumps({'text': f'Parecer sobre o Roteiro (Revisão {tentativas_esp_roteiro}):\n{esp_roteiro_content}', 'reasoning': esp_roteiro_reasoning}, ensure_ascii=False)}")
            else:
                sse_send(f"[Especialista China] Parecer sobre o Roteiro (Revisão {tentativas_esp_roteiro}):\n{esp_roteiro_content}")
                
            rejeitado = (
                "REPROVADO" in esp_roteiro_content.upper()
                or "REPROVADA" in esp_roteiro_content.upper()
                or "NÃO AUTORIZADO" in esp_roteiro_content.upper()
                or "NÃO AUTORIZADA" in esp_roteiro_content.upper()
            )
            deve_alterar = False
            
            if rejeitado:
                sse_send(f"[Sistema] O Especialista China REJEITOU o roteiro na revisão {tentativas_esp_roteiro}. O Roteirista deverá mudar seu trabalho obrigatoriamente.")
                deve_alterar = True
            else:
                # Ponderação do Roteirista sobre as alterações
                sse_send("[Roteirista] Ponderando sobre as alterações sugeridas pelo Especialista China...")
                ponderacao_res = run_with_retry(
                    "Roteirista",
                    lambda: _roteirista_ponderar_roteiro_fallback(g_client, roteiro, esp_roteiro_content),
                    lambda: _roteirista_ponderar_roteiro_primary(or_client, roteiro, esp_roteiro_content),
                    sse_send
                )
                decisao = ponderacao_res.get("decisao", "PROSSEGUIR")
                justificativa = ponderacao_res.get("justificativa", "Sem justificativa.")
                sugestoes_existem = ponderacao_res.get("sugestoes_existem", True)
                
                if sugestoes_existem:
                    sse_send(f"[Roteirista] JSON:{json.dumps({'text': f'Ponderação do Roteirista: A Decisão é {decisao}. Justificativa: {justificativa}', 'reasoning': 'Ponderando se as sugestões pioram o roteiro...'}, ensure_ascii=False)}")
                    if decisao == "ALTERAR":
                        sse_send("[Sistema] Roteirista ponderou que as sugestões NÃO pioram seu trabalho. Alterando o roteiro...")
                        deve_alterar = True
                    else:
                        sse_send("[Sistema] Roteirista ponderou que as sugestões PIORAM o roteiro (ou são desnecessárias). Prosseguindo como está.")
                else:
                    sse_send("[Sistema] Roteiro aprovado sem ressalvas ou sugestões pelo Especialista China. Prosseguindo.")
                
                # Se foi aprovado (com ou sem ressalvas), o Roteirista pondera, mas o fluxo do Especialista acabou (não reenvia)
                roteiro_finalizado = True
                
            if deve_alterar:
                sse_send("[Roteirista] Refazendo o roteiro com base no feedback do Especialista...")
                prompt_correcao = (
                    f"Aqui está o roteiro anterior que deve ser melhorado:\n{json.dumps(roteiro, ensure_ascii=False, indent=2)}\n\n"
                    f"Feedback de correção/rejeição do Especialista China:\n{esp_roteiro_content}\n\n"
                    "Por favor, reescreva o roteiro completo aplicando as correções/sugestões apontadas."
                )
                roteiro_res = run_with_retry(
                    "Roteirista",
                    lambda: _roteirista_fallback(g_client, conto, rot_geral, f"{rot_especifica or ''}\n{prompt_correcao}", rot_arquivo_b64, rot_arquivo_mime),
                    lambda: _roteirista_primary(or_client, conto, rot_geral, f"{rot_especifica or ''}\n{prompt_correcao}", rot_arquivo_b64, rot_arquivo_mime, g_client),
                    sse_send
                )
                raw_roteiro = roteiro_res["content"]
                cleaned = clean_json_text(raw_roteiro)
                roteiro = json.loads(cleaned)
                # Atualiza variáveis locais
                titulo = roteiro.get("titulo", "Conto Taoista")
                paginas = roteiro.get("paginas", [])
                total_paginas = roteiro.get("total_paginas") or len(paginas)
                
                # Salva o roteiro final/atualizado na pasta do conto
                roteiro_cache_path = os.path.join(tale_dir, "roteiro.json")
                with open(roteiro_cache_path, "w", encoding="utf-8") as f:
                    json.dump(roteiro, f, ensure_ascii=False, indent=2)
                _drive_upload(roteiro_cache_path)
                sse_send(f"[Sistema] Roteiro atualizado salvo em: {roteiro_cache_path}")
                
                if tentativas_esp_roteiro >= max_tentativas_esp_roteiro:
                    sse_send(f"[Sistema] Atingido o limite de {max_tentativas_esp_roteiro} revisões do Especialista China. Seguindo o fluxo com o roteiro corrigido.")
                    roteiro_finalizado = True
        
    paginas_salvas = []
    
    # Loop de páginas
    for i, pagina_script in enumerate(paginas, start=1):
        if check_status:
            check_status()
        sse_send(f"[Sistema] === INICIANDO PROCESSAMENTO DA PÁGINA {i} de {total_paginas} ===")
        
        # Caminho do prompt do designer para a página
        prompt_path = os.path.join(tale_dir, f"prompt_pagina_{i}.txt")
        prompt_designer = None
        
        if os.path.exists(prompt_path):
            try:
                sse_send(f"[Sistema] Prompt da página {i} encontrado localmente! Carregando do cache...")
                with open(prompt_path, "r", encoding="utf-8") as f:
                    prompt_designer = f.read().strip()
                sse_send(f"[Designer Oriental] Prompt da Página {i} carregado com sucesso do cache local.")
            except Exception as e:
                sse_send(f"[Sistema] Erro ao carregar prompt cacheado da página {i}: {str(e)}. Criando novo prompt...")
                
        designer_reasoning = None
        if not prompt_designer:
            # 2. Designer Oriental cria o prompt (passando regras do designer e do artista)
            sse_send(f"[Designer Oriental] Criando o prompt visual para a página {i}...")
            designer_res = run_with_retry(
                "Designer Oriental",
                lambda: _designer_primary(g_client, pagina_script, i, total_paginas, ref_image, des_geral, des_especifica, art_geral, art_especifica, des_arquivo_b64, des_arquivo_mime, art_arquivo_b64, art_arquivo_mime, titulo=titulo),
                lambda: _designer_fallback(or_client, pagina_script, i, total_paginas, ref_image, des_geral, des_especifica, art_geral, art_especifica, des_arquivo_b64, des_arquivo_mime, art_arquivo_b64, art_arquivo_mime, titulo=titulo),
                sse_send
            )
            prompt_designer = designer_res["content"]
            designer_reasoning = designer_res.get("reasoning")
            # Salva o prompt na pasta do conto
            with open(prompt_path, "w", encoding="utf-8") as f:
                f.write(prompt_designer)
            _drive_upload(prompt_path)
            sse_send(f"[Sistema] Novo prompt da página {i} salvo na pasta do conto: {prompt_path}")
            
        if designer_reasoning:
            sse_send(f"[Designer Oriental] JSON:{json.dumps({'text': f'Prompt da Página {i}: \"{prompt_designer}\"', 'reasoning': designer_reasoning}, ensure_ascii=False)}")
        else:
            sse_send(f"[Designer Oriental] Prompt da Página {i}: \"{prompt_designer}\"")
        
        revisao_aprovada = False
        especialista_revisao_feita = os.path.exists(os.path.join(especialista_dir, f"parecer_especialista_pagina_{i}.txt"))
        tentativa_revisao = 0
        prompt_atual = prompt_designer
        diretiva_lider_atual = None
        feedback_revisor = None
        feedback_especialista = None
        imagem_final = None
        
        # Caminhos de arquivos da imagem final
        image_path_in_tale = os.path.join(aprovadas_dir, f"pagina_{i}.png")
        
        # Retrocompatibilidade de imagem
        old_image_path = os.path.join(tale_dir, f"pagina_{i}.png")
        if os.path.exists(old_image_path) and not os.path.exists(image_path_in_tale):
            try:
                import shutil
                shutil.move(old_image_path, image_path_in_tale)
            except Exception:
                pass
                
        safe_title = "".join([c if c.isalnum() else "_" for c in titulo]).strip("_")
        filename = f"{safe_title}_pagina_{i}.png"
        filepath = os.path.join(output_dir, filename)
        
        # Verifica se a imagem final já existe na pasta do conto (cache de arte aprovada)
        if os.path.exists(image_path_in_tale):
            try:
                sse_send(f"[Sistema] Imagem final da página {i} encontrada localmente! Carregando do cache...")
                imagem_final = Image.open(image_path_in_tale)
                imagem_final.save(filepath)
                try:
                    from app.drive_upload import upload_image_to_drive
                    upload_image_to_drive(filepath)
                except Exception:
                    pass
                sse_send(f"[Sistema] Página {i} carregada com sucesso do cache e copiada para '{filepath}'")
                paginas_salvas.append(filename)
                revisao_aprovada = True
            except Exception as e:
                sse_send(f"[Sistema] Erro ao carregar imagem cacheada da página {i}: {str(e)}. Redesenhando...")

        # Valida o prompt com o Especialista China antes de enviar para desenho
        if not revisao_aprovada:
            parecer_prompt_path = os.path.join(especialista_dir, f"parecer_especialista_prompt_pagina_{i}.txt")
            if not os.path.exists(parecer_prompt_path) and prompt_designer:
                sse_send(f"[Especialista China] Analisando prompt visual do Designer Oriental para a página {i}...")
                
                def especialista_prompt_call():
                    return _especialista_prompt_primary(
                        g_client, prompt_designer, pagina_script, i, total_paginas,
                        esp_geral, esp_especifica, esp_arquivo_b64, esp_arquivo_mime, titulo=titulo
                    )
                    
                def especialista_prompt_fallback_call():
                    try:
                        sse_send("[Especialista China] Acionando backups no OpenRouter...")
                        return _especialista_prompt_fallback(
                            or_client, prompt_designer, pagina_script, i, total_paginas,
                            esp_geral, esp_especifica, esp_arquivo_b64, esp_arquivo_mime, titulo=titulo
                        )
                    except Exception as e_or:
                        if ant_client:
                            sse_send(f"[Especialista China] Backups do OpenRouter falharam: {str(e_or)}. Acionando Claude 3.5 Sonnet como último recurso...")
                            try:
                                return _especialista_prompt_fallback_claude(
                                    ant_client, prompt_designer, pagina_script, i, total_paginas,
                                    esp_geral, esp_especifica, titulo=titulo
                                )
                            except Exception as e_claude:
                                sse_send(f"[Especialista China] Claude de Backup também falhou: {str(e_claude)}.")
                                raise e_claude
                        raise e_or
                    
                resultado_esp_prompt_res = run_with_retry(
                    "Especialista China",
                    especialista_prompt_call,
                    especialista_prompt_fallback_call,
                    sse_send
                )
                resultado_esp_prompt = resultado_esp_prompt_res["content"]
                esp_prompt_reasoning = resultado_esp_prompt_res.get("reasoning")
                
                with open(parecer_prompt_path, "w", encoding="utf-8") as f:
                    f.write(resultado_esp_prompt)
                _drive_upload(parecer_prompt_path)

                if esp_prompt_reasoning:
                    sse_send(f"[Especialista China] JSON:{json.dumps({'text': f'Parecer do Especialista sobre o Prompt:\\n{resultado_esp_prompt}', 'reasoning': esp_prompt_reasoning}, ensure_ascii=False)}")
                else:
                    sse_send(f"[Especialista China] Parecer do Especialista sobre o Prompt:\\n{resultado_esp_prompt}")
                    
                esp_prompt_rejeitou = (
                    "REPROVADO" in resultado_esp_prompt.upper()
                    or "REPROVADA" in resultado_esp_prompt.upper()
                    or "NÃO AUTORIZADO" in resultado_esp_prompt.upper()
                    or "NÃO AUTORIZADA" in resultado_esp_prompt.upper()
                )
                
                if esp_prompt_rejeitou:
                    sse_send(f"[Sistema] O Especialista China REJEITOU o prompt da página {i}. O Designer Oriental deverá corrigir.")
                    sse_send(f"[Designer Oriental] Corrigindo o prompt da página {i} com base na rejeição do Especialista...")
                    designer_res = run_with_retry(
                        "Designer Oriental",
                        lambda: _designer_primary(g_client, pagina_script, i, total_paginas, ref_image, des_geral, f"{des_especifica or ''}. Critical correction details from China Specialist: {resultado_esp_prompt}", art_geral, art_especifica, des_arquivo_b64, des_arquivo_mime, art_arquivo_b64, art_arquivo_mime, titulo=titulo),
                        lambda: _designer_fallback(or_client, pagina_script, i, total_paginas, ref_image, des_geral, f"{des_especifica or ''}. Critical correction details from China Specialist: {resultado_esp_prompt}", art_geral, art_especifica, des_arquivo_b64, des_arquivo_mime, art_arquivo_b64, art_arquivo_mime, titulo=titulo),
                        sse_send
                    )
                    prompt_designer = designer_res["content"]
                    with open(prompt_path, "w", encoding="utf-8") as f:
                        f.write(prompt_designer)
                    sse_send(f"[Sistema] Prompt corrigido salvo em: {prompt_path}")
                else:
                    # Ponderação do Designer Oriental se houver ressalvas
                    sse_send(f"[Designer Oriental] Ponderando sobre as sugestões do Especialista no prompt da página {i}...")
                    ponderacao_res = run_with_retry(
                        "Designer Oriental",
                        lambda: _artista_ponderar_pagina_primary(g_client, prompt_designer, resultado_esp_prompt),
                        lambda: _artista_ponderar_pagina_fallback(or_client, prompt_designer, resultado_esp_prompt),
                        sse_send
                    )
                    decisao = ponderacao_res.get("decisao", "PROSSEGUIR")
                    justificativa = ponderacao_res.get("justificativa", "Sem justificativa.")
                    sugestoes_existem = ponderacao_res.get("sugestoes_existem", True)
                    
                    if sugestoes_existem:
                        sse_send(f"[Designer Oriental] JSON:{json.dumps({'text': f'Ponderação do Designer: A Decisão é {decisao}. Justificativa: {justificativa}', 'reasoning': 'Ponderando se as sugestões de prompt pioram a arte...'}, ensure_ascii=False)}")
                        if decisao == "ALTERAR":
                            sse_send(f"[Sistema] Designer Oriental aceitou as sugestões. Atualizando prompt...")
                            prompt_designer = f"{prompt_designer}. Implement these suggested changes that you agreed do not worsen the visual work: {resultado_esp_prompt}"
                            with open(prompt_path, "w", encoding="utf-8") as f:
                                f.write(prompt_designer)
                        else:
                            sse_send(f"[Sistema] Designer Oriental decidiu manter o prompt como planejado.")
                    else:
                        sse_send(f"[Sistema] Prompt da página {i} aprovado sem ressalvas.")
            
            # Atualiza o prompt_atual para as tentativas do artista
            prompt_atual = prompt_designer
        
        # Loop de arte e revisão
        while not revisao_aprovada:
            if check_status:
                check_status()
            
            # Re-avalia as instruções e arquivos locais a cada tentativa de desenho/revisão para suportar alterações dinâmicas mid-run
            des_especifica = inst.get("designer", {}).get("especifica")
            des_arquivo_b64 = inst.get("designer", {}).get("arquivo_b64")
            des_arquivo_mime = inst.get("designer", {}).get("arquivo_mime")
            
            art_especifica = inst.get("artista", {}).get("especifica")
            art_arquivo_b64 = inst.get("artista", {}).get("arquivo_b64")
            art_arquivo_mime = inst.get("artista", {}).get("arquivo_mime")
            
            rev_especifica = inst.get("revisor", {}).get("especifica")
            rev_arquivo_b64 = inst.get("revisor", {}).get("arquivo_b64")
            rev_arquivo_mime = inst.get("revisor", {}).get("arquivo_mime")
            
            esp_especifica = inst.get("especialista", {}).get("especifica")
            esp_arquivo_b64 = inst.get("especialista", {}).get("arquivo_b64")
            esp_arquivo_mime = inst.get("especialista", {}).get("arquivo_mime")
            
            tentativa_revisao += 1
            sse_send(f"[Artista] Desenhando a página {i} (Geração {tentativa_revisao})...")
            
            try:
                # Monta o prompt unificado: base do designer + pareceres de rejeição (revisor e/ou especialista) + diretiva do líder
                prompt_a_gerar = prompt_designer
                feedbacks = []
                if feedback_revisor:
                    feedbacks.append(f"Reviewer corrections: {feedback_revisor}")
                if feedback_especialista:
                    feedbacks.append(f"Specialist corrections: {feedback_especialista}")
                STYLE_REMINDER = (
                    "Maintain the full taoist visual style: aged parchment paper texture as full page background, "
                    "warm sepia and antique gold palette, varied panel layout (panoramic + side-by-side + panoramic), "
                    "thin Chinese ornamental panel borders, speech bubbles and narrative boxes with parchment-textured "
                    "background (never white), dramatic god-ray lighting, mystical mist and mountain landscapes, "
                    "and the 道 red seal stamp in the bottom-right corner."
                )
                if feedbacks:
                    prompt_a_gerar = f"{prompt_designer}. CORRECTIONS REQUIRED: {' | '.join(feedbacks)} — {STYLE_REMINDER}"
                else:
                    prompt_a_gerar = f"{prompt_designer}. {STYLE_REMINDER}"
                if diretiva_lider_atual:
                    prompt_a_gerar = f"{prompt_a_gerar}. LEADER DIRECTIVE (Max Priority): {diretiva_lider_atual}"
                    
                # 3. Artista desenha (gerar_imagem_artista com fallback em cascata)
                img_data = gerar_imagem_artista(
                    o_client=o_client,
                    or_client=or_client,
                    g_client=g_client,
                    prompt=prompt_a_gerar,
                    primary_model=artista_model,
                    sse_send=sse_send,
                    poe_key=poe_key,
                    modelos_paths=modelos_paths
                )
                
                if isinstance(img_data, str):
                    img_data = {"url": img_data}
                    
                model_used = img_data.get("model_used", artista_model)
                is_codex = ("codex" in model_used)
                if img_data.get("b64_json"):
                    sse_send(f"[Artista] Processando imagem em Base64 para a página {i}...")
                    import base64
                    image_bytes = base64.b64decode(img_data["b64_json"])
                    imagem_raw = Image.open(io.BytesIO(image_bytes))
                elif img_data.get("url"):
                    sse_send(f"[Artista] Baixando imagem da página {i}...")
                    response = httpx.get(img_data["url"], timeout=60.0)
                    response.raise_for_status()
                    imagem_raw = Image.open(io.BytesIO(response.content))
                else:
                    raise ValueError("Nenhum dado de imagem válido (URL ou B64) retornado pelo Artista.")
                
                # Garante que a imagem final está exatamente no formato vertical 1024x1536 (2:3)
                imagem_final = resize_and_pad_image_to_target(imagem_raw, is_page_1=(i == 1))
                try:
                    filepath_temp = filepath.replace(".png", "_temp.png")
                    imagem_final.save(filepath_temp)
                except Exception as e_save_temp:
                    sse_send(f"[Sistema] Aviso: Não foi possível salvar a imagem temporária para o Líder: {str(e_save_temp)}")
                
                # 4. Revisor valida
                sse_send(f"[Revisor] Inspecionando imagem e textos da página {i}...")
                
                def revisor_primary_call():
                    rev_esp_com_lider = rev_especifica
                    if diretiva_lider_atual:
                        rev_esp_com_lider = f"(DIRETIVA DO LÍDER: {diretiva_lider_atual}) {rev_especifica or ''}"
                    return _revisor_primary(g_client, imagem_final, prompt_designer, i, total_paginas, rev_geral, rev_esp_com_lider, rev_arquivo_b64, rev_arquivo_mime, titulo=titulo)
                    
                def revisor_fallback_call():
                    rev_esp_com_lider = rev_especifica
                    if diretiva_lider_atual:
                        rev_esp_com_lider = f"(DIRETIVA DO LÍDER: {diretiva_lider_atual}) {rev_especifica or ''}"
                    try:
                        sse_send("[Revisor] Acionando backups no OpenRouter...")
                        return _revisor_fallback(or_client, imagem_final, prompt_designer, i, total_paginas, rev_geral, rev_esp_com_lider, rev_arquivo_b64, rev_arquivo_mime, titulo=titulo, sse_send=sse_send)
                    except Exception as e_or:
                        if ant_client:
                            sse_send(f"[Revisor] Backups do OpenRouter falharam: {str(e_or)}. Acionando Claude 3.5 Sonnet como último recurso...")
                            try:
                                return _revisor_fallback_claude(ant_client, imagem_final, prompt_designer, i, total_paginas, rev_geral, rev_esp_com_lider, titulo=titulo)
                            except Exception as e_claude:
                                sse_send(f"[Revisor] Claude de Backup também falhou: {str(e_claude)}.")
                                raise e_claude
                        raise e_or
                
                resultado_revisao_res = run_with_retry(
                    "Revisor",
                    revisor_primary_call,
                    revisor_fallback_call,
                    sse_send
                )
                resultado_revisao = resultado_revisao_res["content"]
                revisor_reasoning = resultado_revisao_res.get("reasoning")
                
                # Salva o parecer do Revisor na pasta do conto
                _status_rev = _status_parecer(resultado_revisao)
                parecer_revisor_path = os.path.join(revisor_dir, f"parecer_revisor_pagina_{i}_{tentativa_revisao}_{_status_rev}.txt")
                with open(parecer_revisor_path, "w", encoding="utf-8") as f:
                    f.write(resultado_revisao)
                _drive_upload(parecer_revisor_path)
                sse_send(f"[Sistema] Parecer {tentativa_revisao} do Revisor para a página {i} salvo em: {parecer_revisor_path}")

                # Salva também no arquivo principal para visualização/compatibilidade
                parecer_revisor_principal_path = os.path.join(revisor_dir, f"parecer_revisor_pagina_{i}.txt")
                with open(parecer_revisor_principal_path, "w", encoding="utf-8") as f:
                    f.write(resultado_revisao)
                _drive_upload(parecer_revisor_principal_path)
                
                if revisor_reasoning:
                    sse_send(f"[Revisor] JSON:{json.dumps({'text': f'Parecer do Revisor: \"{resultado_revisao}\"', 'reasoning': revisor_reasoning}, ensure_ascii=False)}")
                else:
                    sse_send(f"[Revisor] Parecer do Revisor: \"{resultado_revisao}\"")
                
                # 4.5. Especialista China valida (máximo 2 vezes em caso de reprovação)
                revisor_ok = ("APROVADO" in resultado_revisao.upper()) or ("APROVADA" in resultado_revisao.upper())
                
                if not revisor_ok:
                    revisao_aprovada = False
                    feedback_revisor = resultado_revisao
                    _salvar_imagem_rejeitada(imagem_final, tale_dir, i, f"rejeitada_revisor_tentativa_{tentativa_revisao}", model_id=model_used)
                    sse_send(f"[Sistema] Página {i} reprovada pelo Revisor! Feedback registrado para o próximo prompt ao Artista.")
                else:
                    feedback_revisor = None
                    sse_send(f"[Sistema] Página {i} aprovada pelo Revisor.")
                    
                    # Verifica se precisamos chamar o Especialista China
                    k = 1
                    ultimo_parecer_conteudo = None
                    while True:
                        p_path = os.path.join(especialista_dir, f"parecer_especialista_pagina_{i}_{k}.txt")
                        if os.path.exists(p_path):
                            try:
                                with open(p_path, "r", encoding="utf-8") as f:
                                    ultimo_parecer_conteudo = f.read()
                            except Exception:
                                pass
                            k += 1
                        else:
                            break
                    tentativa_atual_esp = k
                    
                    precisa_especialista = False
                    if ultimo_parecer_conteudo is None:
                        precisa_especialista = True
                    else:
                        ultimo_rejeitou = (
                            "REPROVADO" in ultimo_parecer_conteudo.upper()
                            or "REPROVADA" in ultimo_parecer_conteudo.upper()
                            or "NÃO AUTORIZADO" in ultimo_parecer_conteudo.upper()
                            or "NÃO AUTORIZADA" in ultimo_parecer_conteudo.upper()
                        )
                        if ultimo_rejeitou:
                            precisa_especialista = True
                        else:
                            precisa_especialista = False
                    
                    if precisa_especialista:
                        sse_send(f"[Especialista China] Analisando consistência e caracteres chineses da página {i} (Revisão {tentativa_atual_esp})...")
                        
                        def especialista_primary_call():
                            esp_esp_com_lider = esp_especifica
                            if diretiva_lider_atual:
                                esp_esp_com_lider = f"(DIRETIVA DO LÍDER: {diretiva_lider_atual}) {esp_especifica or ''}"
                            return _especialista_pagina_primary(g_client, imagem_final, prompt_designer, pagina_script, i, total_paginas, esp_geral, esp_esp_com_lider, esp_arquivo_b64, esp_arquivo_mime, titulo=titulo)
                            
                        def especialista_fallback_call():
                            esp_esp_com_lider = esp_especifica
                            if diretiva_lider_atual:
                                esp_esp_com_lider = f"(DIRETIVA DO LÍDER: {diretiva_lider_atual}) {esp_especifica or ''}"
                            try:
                                sse_send("[Especialista China] Acionando backups no OpenRouter...")
                                return _especialista_pagina_fallback(or_client, imagem_final, prompt_designer, pagina_script, i, total_paginas, esp_geral, esp_esp_com_lider, esp_arquivo_b64, esp_arquivo_mime, titulo=titulo)
                            except Exception as e_or:
                                if ant_client:
                                    sse_send(f"[Especialista China] Backups do OpenRouter falharam: {str(e_or)}. Acionando Claude 3.5 Sonnet como último recurso...")
                                    try:
                                        return _especialista_pagina_fallback_claude(ant_client, imagem_final, prompt_designer, pagina_script, i, total_paginas, esp_geral, esp_esp_com_lider, titulo=titulo)
                                    except Exception as e_claude:
                                        sse_send(f"[Especialista China] Claude de Backup também falhou: {str(e_claude)}.")
                                        raise e_claude
                                raise e_or
                        
                        resultado_especialista_res = run_with_retry(
                            "Especialista China",
                            especialista_primary_call,
                            especialista_fallback_call,
                            sse_send
                        )
                        resultado_especialista = resultado_especialista_res["content"]
                        especialista_reasoning = resultado_especialista_res.get("reasoning")
                        
                        # Salva o parecer com o número da tentativa e status na pasta do conto
                        _status_esp = _status_parecer(resultado_especialista)
                        parecer_pagina_path = os.path.join(especialista_dir, f"parecer_especialista_pagina_{i}_{tentativa_atual_esp}_{_status_esp}.txt")
                        with open(parecer_pagina_path, "w", encoding="utf-8") as f:
                            f.write(resultado_especialista)
                        _drive_upload(parecer_pagina_path)
                        sse_send(f"[Sistema] Parecer {tentativa_atual_esp} do Especialista China para a página {i} salvo em: {parecer_pagina_path}")

                        # Salva também no arquivo principal para visualização/compatibilidade
                        parecer_principal_path = os.path.join(especialista_dir, f"parecer_especialista_pagina_{i}.txt")
                        with open(parecer_principal_path, "w", encoding="utf-8") as f:
                            f.write(resultado_especialista)
                        _drive_upload(parecer_principal_path)
                        
                        especialista_rejeitou = (
                            "REPROVADO" in resultado_especialista.upper()
                            or "REPROVADA" in resultado_especialista.upper()
                            or "NÃO AUTORIZADO" in resultado_especialista.upper()
                            or "NÃO AUTORIZADA" in resultado_especialista.upper()
                        )
                        
                        if especialista_reasoning:
                            sse_send(f"[Especialista China] JSON:{json.dumps({'text': f'Parecer do Especialista (Revisão {tentativa_atual_esp}):\n{resultado_especialista}', 'reasoning': especialista_reasoning}, ensure_ascii=False)}")
                        else:
                            sse_send(f"[Especialista China] Parecer do Especialista (Revisão {tentativa_atual_esp}):\n{resultado_especialista}")
                        
                        if especialista_rejeitou:
                            feedback_especialista = resultado_especialista
                            _salvar_imagem_rejeitada(imagem_final, tale_dir, i, f"rejeitada_especialista_tentativa_{tentativa_revisao}", model_id=model_used)
                            sse_send(f"[Sistema] O Especialista China REJEITOU a página na revisão {tentativa_atual_esp}. Feedback registrado para o próximo prompt ao Artista.")
                            revisao_aprovada = False
                        else:
                            feedback_especialista = None
                            sse_send(f"[Sistema] Página {i} aprovada pelo Especialista China!")
                            revisao_aprovada = True
                    else:
                        sse_send(f"[Sistema] Página {i} aprovada pelo Especialista China anteriormente.")
                        revisao_aprovada = True
                    
                            
            except Exception as e:
                sse_send(f"[Sistema] Erro na geração/revisão da página {i}: {str(e)}")
                raise e
                
        # Se a imagem final foi gerada e aprovada agora (não veio do cache), salva nos dois diretórios
        if imagem_final and not os.path.exists(image_path_in_tale):
            try:
                imagem_final.save(image_path_in_tale)
                imagem_final.save(filepath)
                try:
                    from app.drive_upload import upload_image_to_drive
                    upload_image_to_drive(filepath)
                except Exception:
                    pass
                sse_send(f"[Sistema] Página {i} salva em '{filepath}' e copiada para a pasta do conto.")
                paginas_salvas.append(filename)
                _registrar_modelo_utilizado(tale_dir, filename, "aprovada", model_used)
                try:
                    filepath_temp = filepath.replace(".png", "_temp.png")
                    if os.path.exists(filepath_temp):
                        os.remove(filepath_temp)
                except Exception:
                    pass
            except Exception as e:
                sse_send(f"[Sistema] Erro ao salvar imagem da página {i}: {str(e)}")
        elif not imagem_final and not os.path.exists(image_path_in_tale):
            sse_send(f"[Sistema] ERRO: Imagem final para página {i} não disponível.")
            
        # 5. Artista reporta término ao Designer Oriental e questiona se há mais
        if i < total_paginas:
            sse_send(f"[Artista] Terminei a página {i}. Há alguma outra página para eu desenhar?")
            sse_send(f"[Designer Oriental] Sim, vamos para a página {i + 1}. Enviando o próximo prompt...")
        else:
            sse_send(f"[Artista] Terminei a página {i}. Há alguma outra página para eu desenhar?")
            sse_send("[Designer Oriental] Não há mais páginas. Esse foi o encerramento do conto!")
            
    sse_send(f"[Sistema] Finalizado! Todas as {total_paginas} páginas salvas no diretório com sucesso.")
    return paginas_salvas


def find_tale_dir_by_filename(filename: str) -> str:
    parts = filename.split("_pagina_")
    if len(parts) < 2:
        return None
    title_prefix = parts[0].lower()
    
    saved_comics_dir = _get_saved_comics_dir()

    if not os.path.exists(saved_comics_dir):
        return None
        
    import unicodedata
    import re
    
    def normalize_str(s: str) -> str:
        s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
        s = re.sub(r'\W+', '', s).lower()
        return s
        
    normalized_prefix = normalize_str(title_prefix)
    
    # 1. Tenta encontrar lendo o roteiro.json de cada subpasta
    for item in os.listdir(saved_comics_dir):
        item_path = os.path.join(saved_comics_dir, item)
        if os.path.isdir(item_path):
            roteiro_path = os.path.join(item_path, "roteiro.json")
            if os.path.exists(roteiro_path):
                try:
                    with open(roteiro_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    titulo = data.get("titulo")
                    if titulo and normalize_str(titulo) == normalized_prefix:
                        return item_path
                except Exception:
                    pass
                    
    # 2. Fallback: Tenta comparar por interseção de palavras significativas (>= 2 palavras em comum)
    def get_significant_words(s: str) -> set:
        s = unicodedata.normalize('NFKD', s).encode('ASCII', 'ignore').decode('utf-8')
        # Subordina underscores para separar palavras
        s = s.replace('_', ' ')
        words = re.findall(r'\b\w{3,}\b', s.lower()) # palavras com 3 ou mais letras
        return set(words)
        
    prefix_words = get_significant_words(title_prefix)
    
    for item in os.listdir(saved_comics_dir):
        item_path = os.path.join(saved_comics_dir, item)
        if os.path.isdir(item_path):
            item_words = get_significant_words(item)
            intersection = prefix_words.intersection(item_words)
            if len(intersection) >= 2:
                return item_path
                
    # 3. Fallback clássico caso nada mais funcione
    for item in os.listdir(saved_comics_dir):
        item_path = os.path.join(saved_comics_dir, item)
        if os.path.isdir(item_path):
            if item.lower().startswith(title_prefix):
                return item_path
    return None



def execute_page_edit(tale_dir: str, filename: str, instruction: str, keys: dict, artista_model: str = "codex/gpt-image-2") -> bool:
    parts = filename.split("_pagina_")
    if len(parts) < 2:
        raise ValueError("Formato de arquivo inválido.")
    page_part = parts[1].replace(".png", "")
    try:
        page_num = int(page_part)
    except ValueError:
        raise ValueError("Número da página inválido.")
        
    prompt_path = os.path.join(tale_dir, f"prompt_pagina_{page_num}.txt")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            original_prompt = f.read().strip()
    else:
        roteiro_path = os.path.join(tale_dir, "roteiro.json")
        if os.path.exists(roteiro_path):
            with open(roteiro_path, "r", encoding="utf-8") as f:
                roteiro = json.load(f)
            paginas = roteiro.get("paginas", [])
            page_data = None
            for p in paginas:
                if p.get("pagina_numero") == page_num:
                    page_data = p
                    break
            if page_data:
                quadrinhos = page_data.get("quadrinhos", [])
                descs = [q.get("descricao_visual", "") for q in quadrinhos]
                original_prompt = f"A comic book page with {len(quadrinhos)} panels. Description: " + " ".join(descs)
            else:
                original_prompt = "A comic book page in traditional Chinese ink wash painting style."
        else:
            original_prompt = "A comic book page in traditional Chinese ink wash painting style."
            
    prompt_atual = f"{original_prompt}. USER REQUESTED EDIT: {instruction}. Style: traditional Chinese ink wash painting."
    
    g_key = keys.get("gemini_api_key") or os.getenv("GEMINI_API_KEY")
    o_key = keys.get("openai_api_key") or os.getenv("OPENAI_API_KEY")
    or_key = keys.get("openrouter_api_key") or os.getenv("OPENROUTER_API_KEY")
    poe_key = keys.get("poe_api_key") or os.getenv("POE_API_KEY")
    
    if not o_key:
        raise ValueError("Chave de API da OpenAI não disponível para redesenho.")
    if not or_key:
        raise ValueError("Chave de API do OpenRouter não disponível para redesenho.")
        
    o_client = openai.OpenAI(api_key=o_key, timeout=120.0)
    or_client = openai.OpenAI(base_url="https://openrouter.ai/api/v1", api_key=or_key, timeout=120.0)
    
    g_client = None
    if g_key:
        g_client = genai.Client(api_key=g_key)
        
    def dummy_sse(msg):
        pass
        
    img_data = gerar_imagem_artista(
        o_client=o_client,
        or_client=or_client,
        g_client=g_client,
        prompt=prompt_atual,
        primary_model=artista_model,
        sse_send=dummy_sse,
        poe_key=poe_key
    )
    
    if isinstance(img_data, str):
        img_data = {"url": img_data}
        
    if img_data.get("b64_json"):
        import base64
        image_bytes = base64.b64decode(img_data["b64_json"])
        imagem_raw = Image.open(io.BytesIO(image_bytes))
    elif img_data.get("url"):
        response = httpx.get(img_data["url"], timeout=60.0)
        response.raise_for_status()
        imagem_raw = Image.open(io.BytesIO(response.content))
    else:
        raise ValueError("Nenhum dado de imagem válido retornado pelo Artista.")
        
    imagem_final = resize_and_pad_image_to_target(imagem_raw, is_page_1=(page_num == 1))
    
    model_used = img_data.get("model_used", artista_model)
    image_path_in_tale = os.path.join(tale_dir, "artista", "aprovadas", f"pagina_{page_num}.png")
    if os.path.exists(image_path_in_tale):
        _salvar_imagem_rejeitada(image_path_in_tale, tale_dir, page_num, "substituida_edicao", model_id=model_used)
        
    saved_comics_dir = _get_saved_comics_dir()
    os.makedirs(saved_comics_dir, exist_ok=True)
    filepath = os.path.join(saved_comics_dir, filename)
    
    imagem_final.save(image_path_in_tale)
    imagem_final.save(filepath)
    try:
        from app.drive_upload import upload_image_to_drive
        upload_image_to_drive(filepath)
    except Exception:
        pass
    _registrar_modelo_utilizado(tale_dir, filename, "aprovada", model_used)
    return True

