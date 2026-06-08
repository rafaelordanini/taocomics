filepath = "/Users/rafaelordanini/.gemini/antigravity/brain/a26d480a-c4c6-41a5-affd-7b00452bec48/walkthrough.md"
new_content = """

### 🛠️ Integração do Codex CLI (ChatGPT Go) e Priorização de Imagens

Concluímos a integração do DALL-E 3 via aplicativo Codex local (ChatGPT Go) como o modelo padrão e de prioridade máxima para a geração das ilustrações da HQ!

#### 1. Implementação do Helper Codex
- Criamos o wrapper `_generar_imagem_codex` em [app/agents.py](file:///Users/rafaelordanini/Desktop/HQ/app/agents.py) que:
  - Invoca o binário oficial do Codex CLI em `/Applications/Codex.app/Contents/Resources/codex`.
  - Passa o parâmetro `--dangerously-bypass-approvals-and-sandbox` para evitar interações no terminal e permitir execução direta.
  - Utiliza `stdin=subprocess.DEVNULL` para evitar bloqueios de terminal e um `timeout` generoso de 300 segundos, permitindo que a IA do Codex execute seus múltiplos passos assíncronos.
  - Monitora o diretório de imagens temporárias do Codex (`~/.codex/generated_images`), detecta o novo arquivo gerado de proporção vertical, converte-o para base64 e o entrega de forma transparente para o pipeline.

#### 2. Priorização e Cascata de Modelos
- Mapeamos a opção `"codex/gpt-image-2"` para invocar o helper do Codex no Artista.
- Atualizamos a lista de backups (`ordered_backups` em `gerar_imagem_artista`) colocando `"codex/gpt-image-2"` no topo como a primeira escolha de geração.
- Atualizamos os parâmetros padrão `artista_model` para `"codex/gpt-image-2"` em `processar_conto_taoista` e `execute_page_edit`.
- Atualizamos as rotas `/api/generate` e `/api/edit-page` no FastAPI (`app/main.py`) para definir `"codex/gpt-image-2"` como fallback/padrão default.

#### 3. Atualização do Frontend
- Inserimos a opção `ChatGPT Go / Codex CLI - GPT-Image-2` no dropdown `#artista-model` em `index.html` e a selecionamos por padrão.
- Ao carregar, o frontend agora inicia o pipeline com o Codex CLI por default.

#### 4. Validação e Testes
- **`scratch/test_codex_integration.py`**: Criamos um script que testa a geração real via Codex. O Codex CLI gerou com sucesso uma imagem no formato PNG e tamanho `1024x1536` pixels (2:3) em aproximadamente 75 segundos, validando a resolução exata e a ausência de diálogos bloqueantes.
- **Suítes de Integração**: Executamos com sucesso `test_especialista_rejection_flow.py` e `test_lider_files.py` para garantir que o pipeline e as interfaces de mock continuem funcionando perfeitamente sem falhas de regressão.
"""

with open(filepath, "r", encoding="utf-8") as f:
    orig = f.read()

# Append if not already present
if "Integração do Codex CLI" not in orig:
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(orig.rstrip() + new_content)
    print("Walkthrough successfully updated!")
else:
    print("Walkthrough already contains Codex section.")
