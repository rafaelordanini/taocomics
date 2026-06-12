// Banco de contos taoístas pré-carregados para facilitar testes
const SAMPLE_TALES = {
    borboleta: `Certa vez, Zhuangzi sonhou que era uma borboleta, voando alegremente de lá para cá, de flor em flor, sem saber que era Zhuangzi. 

De repente, ele acordou e viu-se Zhuangzi novamente, deitado em sua cama. 

Mas agora ele não sabia se era Zhuangzi que havia sonhado ser uma borboleta, ou se era uma borboleta que agora estava sonhando ser Zhuangzi. 

Entre Zhuangzi e a borboleta deve haver alguma diferença. Isso é o que chamamos de a Transmutação das Coisas, onde tudo no universo está conectado em eterno fluxo e mudança.`,

    inutil: `Um carpinteiro viajante chamado Shi viu um carvalho gigantesco e sagrado perto de um templo da aldeia. A árvore era tão imensa que sua sombra cobria mil bois e centenas de barcos poderiam ser esculpidos de seus galhos.

O carpinteiro nem parou para olhar e continuou andando. Seu aprendiz perguntou: "Mestre, por que ignora essa magnífica árvore?"

Shi respondeu: "Essa madeira é inútil! Se você fizer um barco com ela, ele afundará. Se fizer ferramentas, elas quebrarão. Se fizer caixões, apodrecerão rapidamente. É uma árvore sem valor comercial, por isso cresceu tanto."

À noite, o espírito do carvalho apareceu em sonho ao carpinteiro e disse: "Com quem você está me comparando? Às árvores frutíferas que são cortadas e machucadas para darem seus frutos? Se eu fosse útil para você, eu já teria sido cortado há muito tempo. Minha inutilidade é o segredo do meu poder e da minha sobrevivência celestial."`,

    rio: `Um jovem discípulo perguntou a um sábio mestre taoísta como viver em paz em um mundo de tanto caos. O mestre levou-o até a margem de um rio de forte correnteza.

O mestre ordenou: "Pule e lute contra o rio. Nade contra a corrente com todas as suas forças." O jovem obedeceu, mas em poucos minutos estava exausto, engolindo água e quase se afogou antes de ser puxado de volta pelo mestre.

O mestre então disse: "Agora, pule novamente, mas desta vez não nade. Apenas relaxe o corpo, deite de costas e flutue." O discípulo pulou e, para sua surpresa, o rio o carregou suavemente ao longo de seu curso, sem esforço.

O mestre caminhando pela margem gritou: "Isso é o Tao. A vida é como a água do rio. A dor surge quando você resiste e tenta forçar o caminho. A paz surge quando você se torna um com o fluxo do destino. Ceder não é fraqueza, é a suprema força do bambu que verga mas não quebra."`
};

// Carrega o conto selecionado no input
function loadSampleTale() {
    const select = document.getElementById("sample-tales");
    const textarea = document.getElementById("tale-input");
    const val = select.value;
    
    if (val && SAMPLE_TALES[val]) {
        textarea.value = SAMPLE_TALES[val];
    } else {
        textarea.value = "";
    }
    saveInputsToLocalStorage();
}

let refImageBase64 = null;

const agentFiles = {
    roteirista: { base64: null, mime: null, name: null },
    designer: { base64: null, mime: null, name: null },
    artista: { base64: null, mime: null, name: null },
    revisor: { base64: null, mime: null, name: null },
    especialista: { base64: null, mime: null, name: null }
};

function handleAgentFileUpload(agent, input) {
    const file = input.files[0];
    const statusSpan = document.getElementById(`${agent}-arquivo-nome`);
    if (file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            agentFiles[agent] = {
                base64: e.target.result.split(',')[1],
                mime: file.type,
                name: file.name
            };
            statusSpan.innerText = file.name;
            statusSpan.classList.add("has-file");
        };
        reader.readAsDataURL(file);
    } else {
        agentFiles[agent] = { base64: null, mime: null, name: null };
        statusSpan.innerText = "Nenhum arquivo";
        statusSpan.classList.remove("has-file");
    }
}

const INPUT_IDS = [
    "gemini-key",
    "openai-key",
    "openrouter-key",
    "poe-key",
    "roteirista-especifica",
    "designer-especifica",
    "artista-especifica",
    "revisor-especifica",
    "especialista-especifica",
    "tale-input",
    "artista-model"
];

function saveInputsToLocalStorage() {
    INPUT_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            localStorage.setItem(`taocomics_${id}`, el.value);
        }
    });
}

function restoreInputsFromLocalStorage() {
    INPUT_IDS.forEach(id => {
        const el = document.getElementById(id);
        const stored = localStorage.getItem(`taocomics_${id}`);
        if (el && stored !== null) {
            el.value = stored;
        }
    });
}

// Inicializa a galeria de imagens e restaura o estado anterior
document.addEventListener("DOMContentLoaded", () => {
    refreshGallery();
    
    // Restaura os campos do localStorage se existirem
    restoreInputsFromLocalStorage();
    
    // Carrega saldos iniciais de APIs
    updateBalances();
    
    // Adiciona listeners para salvar inputs automaticamente conforme digita
    INPUT_IDS.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener("input", saveInputsToLocalStorage);
            el.addEventListener("change", saveInputsToLocalStorage);
        }
    });
    
    // Listener para carregar a imagem de referência
    const fileInput = document.getElementById("ref-image");
    if (fileInput) {
        fileInput.addEventListener("change", (e) => {
            const file = e.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = (evt) => {
                    refImageBase64 = evt.target.result.split(",")[1];
                };
                reader.readAsDataURL(file);
            } else {
                refImageBase64 = null;
            }
        });
    }
    
    // Listener para limpar o console
    const btnClearConsole = document.getElementById("btn-clear-console");
    if (btnClearConsole) {
        btnClearConsole.addEventListener("click", () => {
            const consoleMessages = document.getElementById("console-messages");
            if (consoleMessages) {
                consoleMessages.innerHTML = "";
            }
        });
    }
});

// Sincroniza todos os arquivos com o Google Drive
async function driveSyncAll() {
    const btn = document.getElementById("btn-drive-sync");
    const status = document.getElementById("drive-sync-status");
    btn.disabled = true;
    btn.querySelector("span.material-icons-round").textContent = "sync";
    btn.querySelector("span.material-icons-round").classList.add("animate-spin");
    status.textContent = "Sincronizando...";
    status.style.color = "var(--text-secondary)";
    try {
        const res = await fetch("/api/drive-sync-all", { method: "POST" });
        const data = await res.json();
        if (data.errors && data.errors.length > 0) {
            status.textContent = `✓ ${data.uploaded} enviados  ✗ ${data.failed} falharam`;
            status.style.color = "#f39c12";
        } else if (data.uploaded === 0) {
            status.textContent = "Nenhum arquivo enviado. Verifique as credenciais do Drive.";
            status.style.color = "#e74c3c";
        } else {
            status.textContent = `✓ ${data.uploaded} arquivos enviados ao Drive`;
            status.style.color = "#27ae60";
        }
    } catch (e) {
        status.textContent = "Erro ao sincronizar: " + e.message;
        status.style.color = "#e74c3c";
    } finally {
        btn.disabled = false;
        btn.querySelector("span.material-icons-round").classList.remove("animate-spin");
        btn.querySelector("span.material-icons-round").textContent = "cloud_sync";
    }
}

// Atualiza a lista de imagens na galeria
async function refreshGallery() {
    try {
        const res = await fetch("/api/comics");
        const data = await res.json();
        const grid = document.getElementById("gallery-grid");
        
        if (data.images && data.images.length > 0) {
            grid.innerHTML = "";
            data.images.forEach(imgName => {
                const card = document.createElement("div");
                card.className = "comic-page-card";
                
                // Formata o título baseado no nome do arquivo
                // Ex: O_Sonho_da_Borboleta_pagina_1.png -> O Sonho da Borboleta (Página 1)
                let displayName = imgName.replace(".png", "").replace(/_/g, " ");
                displayName = displayName.replace("pagina", "Pág.");
                
                const timestamp = new Date().getTime();
                card.innerHTML = `
                    <div class="comic-image-container" onclick="openImage('/saved_comics/${imgName}?t=${timestamp}', '${displayName}', '${imgName}')">
                        <img src="/saved_comics/${imgName}?t=${timestamp}" alt="${displayName}">
                        <div class="comic-overlay">
                            <span class="material-icons-round">zoom_in</span>
                        </div>
                    </div>
                    <div class="comic-info">
                        <span class="comic-title">${displayName}</span>
                        <div class="comic-actions">
                            <a href="/saved_comics/${imgName}?t=${timestamp}" download="${imgName}" class="btn-icon" title="Baixar imagem">
                                <span class="material-icons-round">download</span>
                            </a>
                            <button class="btn-icon btn-delete" title="Excluir imagem" onclick="deleteComic('${imgName}', '${displayName}')">
                                <span class="material-icons-round">delete</span>
                            </button>
                        </div>
                    </div>
                `;
                grid.appendChild(card);
            });
        }
    } catch (err) {
        console.error("Erro ao carregar a galeria:", err);
    }
}

// Exclui permanentemente uma imagem gerada
async function deleteComic(imgName, displayName) {
    if (!confirm(`Excluir "${displayName}"?\n\nEsta ação remove o arquivo permanentemente e não pode ser desfeita.`)) {
        return;
    }
    try {
        const res = await fetch(`/api/comics/${encodeURIComponent(imgName)}`, { method: "DELETE" });
        const data = await res.json();
        if (data.error) {
            alert("Erro ao excluir: " + data.error);
            return;
        }
        await refreshGallery();
    } catch (err) {
        console.error("Erro ao excluir a imagem:", err);
        alert("Erro ao excluir a imagem.");
    }
}

// ----------------------------------------------------------------------
// Explorador de Arquivos — navega pastas, pareceres e imagens do servidor
// ----------------------------------------------------------------------
let browserCurrentPath = "";

function openFileBrowser() {
    document.getElementById("browser-modal").style.display = "flex";
    browseTo("");
}

function closeFileBrowser() {
    document.getElementById("browser-modal").style.display = "none";
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

async function browseTo(path) {
    browserCurrentPath = path;
    const viewer = document.getElementById("browser-viewer");
    viewer.style.display = "none";
    viewer.innerHTML = "";
    const listEl = document.getElementById("browser-list");
    listEl.style.display = "block";
    listEl.innerHTML = `<div class="browser-loading">Carregando...</div>`;

    try {
        const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        renderBreadcrumb(data.path || "");
        if (data.error) {
            listEl.innerHTML = `<div class="browser-loading">Erro: ${data.error}</div>`;
            return;
        }
        if (!data.entries || data.entries.length === 0) {
            listEl.innerHTML = `<div class="browser-loading">Pasta vazia.</div>`;
            return;
        }
        listEl.innerHTML = "";
        data.entries.forEach(entry => {
            const row = document.createElement("div");
            row.className = "browser-row";
            let icon, meta, onclick;
            if (entry.type === "dir") {
                icon = "folder";
                meta = `${entry.count} ${entry.count === 1 ? "item" : "itens"}`;
                onclick = `browseTo('${entry.path.replace(/\\/g, "/").replace(/'/g, "\\'")}')`;
            } else if (entry.is_image) {
                icon = "image";
                meta = formatSize(entry.size);
                onclick = `viewBrowserImage('${entry.path.replace(/\\/g, "/").replace(/'/g, "\\'")}', '${entry.name.replace(/'/g, "\\'")}')`;
            } else if (entry.is_text) {
                icon = "description";
                meta = formatSize(entry.size);
                onclick = `viewBrowserText('${entry.path.replace(/\\/g, "/").replace(/'/g, "\\'")}', '${entry.name.replace(/'/g, "\\'")}')`;
            } else {
                icon = "insert_drive_file";
                meta = formatSize(entry.size);
                onclick = "";
            }
            row.innerHTML = `
                <div class="browser-row-main" onclick="${onclick}">
                    <span class="material-icons-round browser-icon ${entry.type === 'dir' ? 'is-dir' : ''}">${icon}</span>
                    <span class="browser-name">${entry.name}</span>
                    <span class="browser-meta">${meta}</span>
                </div>
                <button class="btn-icon btn-delete" title="Excluir" onclick="deleteBrowserEntry('${entry.path.replace(/\\/g, "/").replace(/'/g, "\\'")}', '${entry.name.replace(/'/g, "\\'")}', ${entry.type === 'dir'})">
                    <span class="material-icons-round">delete</span>
                </button>
            `;
            listEl.appendChild(row);
        });
    } catch (err) {
        console.error("Erro ao navegar:", err);
        listEl.innerHTML = `<div class="browser-loading">Erro ao carregar.</div>`;
    }
}

function renderBreadcrumb(path) {
    const bc = document.getElementById("browser-breadcrumb");
    const parts = path ? path.split("/") : [];
    let html = `<span class="crumb" onclick="browseTo('')">saved_comics</span>`;
    let acc = "";
    parts.forEach(part => {
        acc = acc ? acc + "/" + part : part;
        html += ` <span class="crumb-sep">/</span> <span class="crumb" onclick="browseTo('${acc.replace(/'/g, "\\'")}')">${part}</span>`;
    });
    bc.innerHTML = html;
}

async function viewBrowserText(path, name) {
    const listEl = document.getElementById("browser-list");
    const viewer = document.getElementById("browser-viewer");
    listEl.style.display = "none";
    viewer.style.display = "block";
    viewer.innerHTML = `<div class="browser-loading">Carregando...</div>`;
    try {
        const res = await fetch(`/api/browse-file?path=${encodeURIComponent(path)}`);
        const data = await res.json();
        const content = data.error ? ("Erro: " + data.error) : data.content;
        viewer.innerHTML = `
            <div class="viewer-header">
                <button class="btn-icon" onclick="browseTo(browserCurrentPath)" title="Voltar"><span class="material-icons-round">arrow_back</span></button>
                <span class="viewer-title">${name}</span>
            </div>
            <pre class="viewer-text"></pre>
        `;
        viewer.querySelector(".viewer-text").textContent = content;
    } catch (err) {
        viewer.innerHTML = `<div class="browser-loading">Erro ao abrir o arquivo.</div>`;
    }
}

function viewBrowserImage(path, name) {
    const listEl = document.getElementById("browser-list");
    const viewer = document.getElementById("browser-viewer");
    listEl.style.display = "none";
    viewer.style.display = "block";
    viewer.innerHTML = `
        <div class="viewer-header">
            <button class="btn-icon" onclick="browseTo(browserCurrentPath)" title="Voltar"><span class="material-icons-round">arrow_back</span></button>
            <span class="viewer-title">${name}</span>
            <a href="/api/browse-raw?path=${encodeURIComponent(path)}" download="${name}" class="btn-icon" title="Baixar"><span class="material-icons-round">download</span></a>
        </div>
        <img class="viewer-image" src="/api/browse-raw?path=${encodeURIComponent(path)}&t=${Date.now()}" alt="${name}">
    `;
}

async function deleteBrowserEntry(path, name, isDir) {
    const tipo = isDir ? "a pasta" : "o arquivo";
    const extra = isDir ? "\n\nTODO o conteúdo da pasta será removido." : "";
    if (!confirm(`Excluir ${tipo} "${name}"?${extra}\n\nEsta ação é permanente e não pode ser desfeita.`)) {
        return;
    }
    try {
        const res = await fetch(`/api/browse?path=${encodeURIComponent(path)}`, { method: "DELETE" });
        const data = await res.json();
        if (data.error) {
            alert("Erro ao excluir: " + data.error);
            return;
        }
        await browseTo(browserCurrentPath);
        refreshGallery();
    } catch (err) {
        console.error("Erro ao excluir:", err);
        alert("Erro ao excluir.");
    }
}

// ----------------------------------------------------------------------
// Fila de processamento em lote (vários .txt, um conto por vez)
// ----------------------------------------------------------------------
let batchPollTimer = null;
let batchFollowingSession = null;

async function enqueueBatchFiles(files) {
    if (!files || files.length === 0) return;
    const contos = [];
    for (const f of files) {
        const texto = await f.text();
        if (texto.trim()) contos.push({ nome: f.name, texto });
    }
    document.getElementById("batch-files").value = "";
    if (contos.length === 0) { alert("Nenhum arquivo com conteúdo válido."); return; }

    const payload = Object.assign({ contos }, collectGenerationConfig());
    try {
        const res = await fetch("/api/generate-batch", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data.error) { alert("Erro: " + data.error); return; }
        startBatchPolling();
    } catch (err) {
        console.error("Erro ao enfileirar contos:", err);
        alert("Erro ao enviar os contos.");
    }
}

// Reúne a mesma configuração usada na geração individual (chaves, modelo, instruções)
function collectGenerationConfig() {
    const cfg = { instrucoes: getCurrentInstructions() };
    const modelSel = document.getElementById("artista-model");
    if (modelSel) cfg.artista_model = modelSel.value;
    const keyIds = {
        gemini_api_key: "gemini-key", openai_api_key: "openai-key",
        openrouter_api_key: "openrouter-key", poe_api_key: "poe-key",
        anthropic_api_key: "anthropic-key"
    };
    for (const [k, id] of Object.entries(keyIds)) {
        const el = document.getElementById(id);
        if (el && el.value.trim()) cfg[k] = el.value.trim();
    }
    return cfg;
}

function startBatchPolling() {
    document.getElementById("batch-queue-panel").style.display = "block";
    if (batchPollTimer) return;
    batchPollTimer = setInterval(refreshBatchQueue, 4000);
    refreshBatchQueue();
}

async function refreshBatchQueue() {
    try {
        const res = await fetch("/api/queue");
        const data = await res.json();
        const list = document.getElementById("batch-queue-list");
        const items = data.queue || [];
        if (items.length === 0) {
            list.innerHTML = `<div style="font-size:0.78rem;color:var(--text-secondary);text-align:center;padding:0.4rem;">Fila vazia.</div>`;
            return;
        }
        const icons = { aguardando: "schedule", processando: "autorenew", concluido: "check_circle", erro: "error" };
        list.innerHTML = "";
        items.forEach(it => {
            const row = document.createElement("div");
            row.className = `batch-item batch-${it.status}`;
            const canRemove = it.status !== "processando";
            row.innerHTML = `
                <span class="material-icons-round batch-icon ${it.status === 'processando' ? 'spinning' : ''}">${icons[it.status] || 'help'}</span>
                <span class="batch-name" title="${it.erro || it.nome}">${it.nome}</span>
                <span class="batch-status">${it.status}</span>
                ${canRemove ? `<button class="btn-icon btn-delete" style="padding:0.15rem" onclick="removeBatchItem('${it.id}')"><span class="material-icons-round" style="font-size:1rem">close</span></button>` : ""}
            `;
            list.appendChild(row);
        });

        // Acompanha automaticamente no console o conto em processamento
        const active = items.find(it => it.status === "processando" && it.session_id);
        if (active && active.session_id !== batchFollowingSession) {
            batchFollowingSession = active.session_id;
            followSession(active.session_id, active.nome);
        }

        // Para o polling quando tudo terminou
        if (items.every(it => it.status === "concluido" || it.status === "erro")) {
            clearInterval(batchPollTimer);
            batchPollTimer = null;
            batchFollowingSession = null;
            refreshGallery();
        }
    } catch (err) {
        console.error("Erro ao consultar a fila:", err);
    }
}

async function removeBatchItem(id) {
    try {
        await fetch(`/api/queue/${id}`, { method: "DELETE" });
        refreshBatchQueue();
    } catch (err) { console.error(err); }
}

// Acompanha no console as mensagens de uma sessão da fila (sem iniciar geração nova)
async function followSession(sessionId, nome) {
    window.activeSessionId = sessionId;
    window.activePolling = true;
    addAgentBubble("Sistema", `Acompanhando o conto da fila: ${nome || sessionId}`, "sistema");

    let since = 0;
    let finished = false;
    while (!finished && window.activeSessionId === sessionId) {
        await new Promise(r => setTimeout(r, 2000));
        let pollData;
        try {
            const res = await fetch(`/api/session/${sessionId}/messages?since=${since}`);
            pollData = await res.json();
        } catch (e) { continue; }
        const msgs = pollData.messages || [];
        for (const msg of msgs) {
            processAgentMessage(msg);
            if (msg === "[FIM]" || msg.startsWith("[ERRO]")) finished = true;
        }
        since += msgs.length;
        if (pollData.done) finished = true;
    }
    refreshGallery();
}

async function doLogout() {
    await fetch("/api/logout", { method: "POST" });
    window.location.href = "/login";
}

function getCurrentInstructions() {
    return {
        roteirista: {
            geral: null,
            especifica: document.getElementById("roteirista-especifica").value.trim() || null,
            arquivo_b64: agentFiles.roteirista.base64 || null,
            arquivo_mime: agentFiles.roteirista.mime || null
        },
        designer: {
            geral: null,
            especifica: document.getElementById("designer-especifica").value.trim() || null,
            arquivo_b64: agentFiles.designer.base64 || null,
            arquivo_mime: agentFiles.designer.mime || null
        },
        artista: {
            geral: null,
            especifica: document.getElementById("artista-especifica").value.trim() || null,
            arquivo_b64: agentFiles.artista.base64 || null,
            arquivo_mime: agentFiles.artista.mime || null
        },
        revisor: {
            geral: null,
            especifica: document.getElementById("revisor-especifica").value.trim() || null,
            arquivo_b64: agentFiles.revisor.base64 || null,
            arquivo_mime: agentFiles.revisor.mime || null
        },
        especialista: {
            geral: null,
            especifica: document.getElementById("especialista-especifica").value.trim() || null,
            arquivo_b64: agentFiles.especialista.base64 || null,
            arquivo_mime: agentFiles.especialista.mime || null
        }
    };
}

// Inicia o processo de conversão via polling
async function startGeneration() {
    const conto = document.getElementById("tale-input").value.trim();

    const geminiKey = document.getElementById("gemini-key").value.trim();
    const openaiKey = document.getElementById("openai-key").value.trim();
    const openrouterKey = document.getElementById("openrouter-key").value.trim();
    const poeKey = document.getElementById("poe-key") ? document.getElementById("poe-key").value.trim() : "";

    if (!conto) {
        alert("Por favor, digite ou selecione um conto taoísta.");
        return;
    }

    const btn = document.getElementById("btn-generate");
    const controls = document.getElementById("generation-controls");
    const btnPause = document.getElementById("btn-pause");
    const btnResume = document.getElementById("btn-resume");
    const btnCancel = document.getElementById("btn-cancel");
    const consoleMessages = document.getElementById("console-messages");
    const statusDot = document.getElementById("status-dot");
    const statusText = document.getElementById("status-text");
    const footer = document.getElementById("console-footer");
    const progressBar = document.getElementById("progress-bar");

    // Altera estados visuais
    btn.disabled = true;
    btnPause.disabled = false;
    btnResume.disabled = true;
    btnCancel.disabled = false;

    consoleMessages.innerHTML = "";
    statusDot.className = "status-dot active";
    statusText.innerText = "Executando agentes...";
    footer.style.display = "block";
    progressBar.style.width = "5%";

    addSystemMessage("Orquestrador de Agentes iniciado. Enviando conto taoísta para processamento...");

    // Captura as instruções específicas de cada agente (as gerais ficam em arquivos MD no servidor)
    const instrucoes = getCurrentInstructions();

    // Flag para cancelamento local do polling
    window.activePolling = true;
    window.activeReader = null; // mantido para compatibilidade com cancelGeneration

    try {
        const response = await fetch("/api/generate", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                conto: conto,
                gemini_api_key: geminiKey || null,
                openai_api_key: openaiKey || null,
                openrouter_api_key: openrouterKey || null,
                poe_api_key: poeKey || null,
                ref_image: refImageBase64,
                instrucoes: instrucoes,
                artista_model: document.getElementById("artista-model").value
            })
        });

        if (!response.ok) {
            throw new Error(`Erro no servidor: ${response.statusText}`);
        }

        const data = await response.json();
        if (data.error) {
            throw new Error(data.error);
        }

        const sessionId = data.session_id;
        window.activeSessionId = sessionId;

        // Polling a cada 2s
        let since = 0;
        let finished = false;

        while (!finished && window.activePolling) {
            await new Promise(resolve => setTimeout(resolve, 2000));

            if (!window.activePolling) break;

            let pollData;
            try {
                const pollRes = await fetch(`/api/session/${sessionId}/messages?since=${since}`);
                pollData = await pollRes.json();
            } catch (pollErr) {
                // Erro de rede temporário — tenta novamente no próximo ciclo
                continue;
            }

            const msgs = pollData.messages || [];
            for (const msg of msgs) {
                if (msg === "[FIM]" || msg.startsWith("[ERRO]")) {
                    processAgentMessage(msg);
                    finished = true;
                    break;
                }
                processAgentMessage(msg);
            }
            since += msgs.length;

            if (pollData.done && !finished) {
                finished = true;
            }
        }
    } catch (err) {
        addAgentBubble("Sistema", `Erro ao conectar ao pipeline de geração: ${err.message}`, "erro");
        statusDot.className = "status-dot";
        statusText.innerText = "Falha";
    } finally {
        window.activePolling = false;
        window.activeReader = null;
        btn.disabled = false;
        btnPause.disabled = true;
        btnResume.disabled = true;
        btnCancel.disabled = true;
        statusDot.className = "status-dot";
        if (statusText.innerText === "Executando agentes...") {
            statusText.innerText = "Finalizado";
            progressBar.style.width = "100%";
        }
        refreshGallery();
    }
}

function escapeHtml(unsafe) {
    if (!unsafe) return "";
    return unsafe
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Processa a mensagem vinda do SSE e cria o balão correspondente na tela
function processAgentMessage(message) {
    const consoleMessages = document.getElementById("console-messages");
    const progressBar = document.getElementById("progress-bar");
    
    if (message.startsWith("[SESSION_ID]")) {
        window.activeSessionId = message.replace("[SESSION_ID]", "").trim();
        return;
    }
    
    if (message.startsWith("[PAUSA]")) {
        const payload = message.replace("[PAUSA]", "").trim();
        const parts = payload.split("|");
        const pageNum = parts[0];
        const tempImg = parts.length > 1 ? parts[1] : "";
        
        const modal = document.getElementById("pause-modal");
        const modalText = document.getElementById("pause-modal-text");
        modalText.innerText = `A página ${pageNum} já foi redesenhada 5 vezes pelo revisor. O que deseja fazer?`;
        
        // Exibir a imagem se tempImg estiver disponível
        const modalImageContainer = document.getElementById("pause-modal-image-container");
        if (modalImageContainer) {
            if (tempImg) {
                // Adiciona timestamp para cache-busting
                modalImageContainer.innerHTML = `<img src="/saved_comics/${tempImg}?t=${Date.now()}" style="width: 100%; max-height: 300px; object-fit: contain; border-radius: 8px; border: 1px solid rgba(255,255,255,0.1); margin-bottom: 12px;" />`;
                modalImageContainer.style.display = "block";
            } else {
                modalImageContainer.style.display = "none";
            }
        }
        
        modal.style.display = "flex";
        
        addAgentBubble("Sistema", `A página ${pageNum} atingiu 5 tentativas de redesenho devido às recusas do Revisor. A geração foi pausada aguardando sua decisão.`, "backup");
        return;
    }
    
    if (message === "[FIM]") {
        addSystemMessage("Processo concluído com sucesso. Todos os agentes finalizaram suas tarefas!");
        progressBar.style.width = "100%";
        return;
    }
    
    if (message.startsWith("[ERRO]")) {
        const errMsg = message.replace("[ERRO]", "").trim();
        addAgentBubble("Sistema", `Erro Crítico no Pipeline: ${errMsg}`, "erro");
        document.getElementById("status-text").innerText = "Erro";
        return;
    }
    
    // Reconhece qual agente está falando
    // Remove o timestamp [DD/MM HH:MM:SS] do início se presente
    let timestamp = "";
    let messageBody = message;
    const tsMatch = message.match(/^(\[\d{2}\/\d{2} \d{2}:\d{2}:\d{2}\])\s*/);
    if (tsMatch) {
        timestamp = tsMatch[1] + " ";
        messageBody = message.slice(tsMatch[0].length);
    }

    let agent = "Sistema";
    let styleClass = "system";
    let text = message;

    if (messageBody.startsWith("[Roteirista]")) {
        agent = "Roteirista";
        styleClass = "roteirista";
        text = timestamp + messageBody.replace("[Roteirista]", "").trim();
        progressBar.style.width = "20%";
    } else if (messageBody.startsWith("[Designer Oriental]")) {
        agent = "Designer Oriental";
        styleClass = "designer";
        text = timestamp + messageBody.replace("[Designer Oriental]", "").trim();
        progressBar.style.width = "40%";
    } else if (messageBody.startsWith("[Artista]")) {
        agent = "Artista (Desenho)";
        styleClass = "artista";
        text = timestamp + messageBody.replace("[Artista]", "").trim();
        progressBar.style.width = "60%";
    } else if (messageBody.startsWith("[Revisor]")) {
        agent = "Revisor (Feedback)";
        styleClass = "revisor";
        text = timestamp + messageBody.replace("[Revisor]", "").trim();
        progressBar.style.width = "75%";
    } else if (messageBody.startsWith("[Especialista China]")) {
        agent = "Especialista China";
        styleClass = "especialista";
        text = timestamp + messageBody.replace("[Especialista China]", "").trim();
        progressBar.style.width = "90%";
    } else if (messageBody.startsWith("[Líder]")) {
        agent = "Líder";
        styleClass = "lider";
        text = timestamp + messageBody.replace("[Líder]", "").trim();
    } else if (messageBody.startsWith("[Orquestrador]")) {
        agent = "Orquestrador";
        styleClass = "system";
        text = timestamp + messageBody.replace("[Orquestrador]", "").trim();
    } else if (messageBody.startsWith("[Sistema]")) {
        agent = "Orquestrador";
        styleClass = "system";
        text = timestamp + messageBody.replace("[Sistema]", "").trim();
    } else {
        text = timestamp + messageBody;
    }
    
    // Identifica se é uma chamada de backup ou retry
    if (text.includes("usando IA de Backup") || text.includes("Acionando IA de backup") || text.includes("tentativa") && text.includes("falhou")) {
        styleClass = "backup";
        agent += " (Backup/Aviso)";
    }
    
    // Tenta decodificar o JSON com reasoning se presente
    let reasoning = null;
    if (text.startsWith("JSON:")) {
        try {
            const rawJson = text.replace("JSON:", "").trim();
            const parsed = JSON.parse(rawJson);
            text = parsed.text || "";
            reasoning = parsed.reasoning || null;
        } catch (e) {
            console.error("Erro ao fazer parse do JSON do agente:", e);
        }
    }
    
    addAgentBubble(agent, text, styleClass, reasoning);
    
    // Atualiza a galeria automaticamente sempre que uma página for salva
    if (text.includes("salva em")) {
        refreshGallery();
    }
}

// Cria balão de mensagem no console
function addAgentBubble(agentName, text, styleClass, reasoning = null) {
    const consoleMessages = document.getElementById("console-messages");
    const bubble = document.createElement("div");
    bubble.className = `agent-bubble ${styleClass}`;
    if (reasoning) {
        bubble.classList.add("has-reasoning");
    }
    
    // Determina o ícone do avatar
    let avatarIcon = "smart_toy";
    if (styleClass === "roteirista") avatarIcon = "history_edu";
    if (styleClass === "designer") avatarIcon = "palette";
    if (styleClass === "artista") avatarIcon = "brush";
    if (styleClass === "revisor") avatarIcon = "fact_check";
    if (styleClass === "especialista") avatarIcon = "menu_book";
    if (styleClass === "lider") avatarIcon = "gavel";
    if (styleClass === "backup") avatarIcon = "report_problem";
    if (styleClass === "erro") avatarIcon = "error";
    if (styleClass === "system") avatarIcon = "terminal";
    
    bubble.innerHTML = `
        <div class="agent-avatar">
            <span class="material-icons-round">${avatarIcon}</span>
        </div>
        <div class="agent-content-box">
            <div class="agent-header">
                <span class="agent-name">${agentName}</span>
                ${reasoning ? `
                    <span class="reasoning-badge" title="Clique para ver o raciocínio">
                        <span class="material-icons-round" style="font-size: 1rem;">psychology</span>
                        Raciocínio
                    </span>` : ''}
            </div>
            <div class="agent-message">${text}</div>
            ${reasoning ? `
                <div class="agent-reasoning-container" style="display: none;">
                    <div class="agent-reasoning-title">
                        <span class="material-icons-round" style="font-size: 0.95rem;">psychology</span>
                        Raciocínio da IA
                    </div>
                    <pre class="agent-reasoning-content">${escapeHtml(reasoning)}</pre>
                </div>` : ''}
        </div>
    `;
    
    if (reasoning) {
        bubble.style.cursor = "pointer";
        bubble.addEventListener("click", (e) => {
            if (e.target.closest('a') || e.target.closest('button')) {
                return;
            }
            const container = bubble.querySelector(".agent-reasoning-container");
            const badge = bubble.querySelector(".reasoning-badge");
            if (container) {
                if (container.style.display === "none") {
                    container.style.display = "block";
                    badge.classList.add("active");
                } else {
                    container.style.display = "none";
                    badge.classList.remove("active");
                }
            }
        });
    }
    
    consoleMessages.appendChild(bubble);
    consoleMessages.scrollTop = consoleMessages.scrollHeight;
}

// Mensagens simples do sistema
function addSystemMessage(text) {
    const consoleMessages = document.getElementById("console-messages");
    const msg = document.createElement("div");
    msg.className = "system-message";
    msg.innerHTML = `
        <span class="material-icons-round">info</span>
        <p>${text}</p>
    `;
    consoleMessages.appendChild(msg);
    consoleMessages.scrollTop = consoleMessages.scrollHeight;
}

// Limpa galeria
async function clearGallery() {
    if (confirm("Deseja realmente limpar a galeria? Isso não excluirá os arquivos físicos da pasta, apenas resetará a visualização.")) {
        document.getElementById("gallery-grid").innerHTML = `
            <div class="empty-gallery">
                <span class="material-icons-round">image</span>
                <p>As páginas aprovadas pelo Revisor aparecerão aqui.</p>
            </div>
        `;
    }
}

// Visualizador de modal em tela cheia
function openImage(src, title, filename) {
    const modal = document.getElementById("image-modal");
    const modalImg = document.getElementById("modal-img");
    const captionText = document.getElementById("modal-caption");
    
    modal.style.display = "flex";
    modalImg.src = src;
    captionText.innerHTML = title;
    window.currentEditingFilename = filename;
}

function closeModal(event) {
    if (event && event.target !== document.getElementById("image-modal") && !event.target.classList.contains("close-modal")) {
        return;
    }
    const modal = document.getElementById("image-modal");
    modal.style.display = "none";
}

function clearEditRefImage() {
    document.getElementById("edit-ref-image").value = "";
    document.getElementById("edit-ref-preview").style.display = "none";
    document.getElementById("edit-ref-img-preview").src = "";
}

async function openEditModal(event) {
    if (event) {
        event.stopPropagation();
    }
    closeModal();
    const modal = document.getElementById("edit-page-modal");
    modal.style.display = "flex";
    document.getElementById("edit-instrucao").value = "";
    clearEditRefImage();

    // Carrega o prompt original da página
    const promptBox = document.getElementById("edit-original-prompt");
    promptBox.textContent = "Carregando...";
    const filename = window.currentEditingFilename;
    if (filename) {
        try {
            const res = await fetch(`/api/page-prompt/${encodeURIComponent(filename)}`);
            const data = await res.json();
            promptBox.textContent = data.prompt || "(Prompt não encontrado para esta página)";
        } catch (e) {
            promptBox.textContent = "(Erro ao carregar prompt)";
        }
    } else {
        promptBox.textContent = "(Nenhuma página selecionada)";
    }

    // Preview da imagem de referência ao selecionar arquivo
    document.getElementById("edit-ref-image").onchange = function() {
        const file = this.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = (e) => {
            document.getElementById("edit-ref-img-preview").src = e.target.result;
            document.getElementById("edit-ref-preview").style.display = "block";
        };
        reader.readAsDataURL(file);
    };
}

function closeEditModal(event) {
    if (event && event.target !== document.getElementById("edit-page-modal") && !event.target.classList.contains("close-modal")) {
        return;
    }
    const modal = document.getElementById("edit-page-modal");
    modal.style.display = "none";
}

async function submitPageEdit() {
    const filename = window.currentEditingFilename;
    const instruction = document.getElementById("edit-instrucao").value.trim();
    
    if (!filename) {
        alert("Nenhuma página selecionada para edição.");
        return;
    }
    if (!instruction) {
        alert("Por favor, descreva a alteração que deseja solicitar ao Artista.");
        return;
    }
    
    const geminiKey = document.getElementById("gemini-key").value.trim();
    const openaiKey = document.getElementById("openai-key").value.trim();
    const openrouterKey = document.getElementById("openrouter-key").value.trim();
    const poeKey = document.getElementById("poe-key") ? document.getElementById("poe-key").value.trim() : "";
    
    const btnSubmit = document.getElementById("btn-submit-edit");
    const loader = document.getElementById("edit-loading-indicator");
    
    btnSubmit.disabled = true;
    loader.style.display = "flex";
    
    // Lê imagem de referência se houver
    let refImageB64 = null;
    const refFile = document.getElementById("edit-ref-image").files[0];
    if (refFile) {
        refImageB64 = await new Promise((resolve) => {
            const reader = new FileReader();
            reader.onload = (e) => resolve(e.target.result.split(",")[1]);
            reader.readAsDataURL(refFile);
        });
    }

    try {
        const response = await fetch("/api/edit-page", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                filename: filename,
                instruction: instruction,
                gemini_api_key: geminiKey || null,
                openai_api_key: openaiKey || null,
                openrouter_api_key: openrouterKey || null,
                poe_api_key: poeKey || null,
                artista_model: document.getElementById("artista-model").value,
                ref_image_b64: refImageB64
            })
        });
        
        const data = await response.json();
        if (data.error) {
            alert(`Erro ao editar página: ${data.error}`);
        } else {
            alert("Página editada com sucesso pelo Artista!");
            closeEditModal();
            refreshGallery();
            addAgentBubble("Artista (Desenho)", `Redesenhei a página ${filename.replace(".png", "").replace(/_/g, " ").replace("pagina", "Pág.")} diretamente conforme sua instrução de alteração: "${instruction}". (Alteração direta bypassando Revisor e Especialista China)`, "artista");
        }
    } catch (err) {
        alert(`Erro de rede ao enviar solicitação de edição: ${err.message}`);
    } finally {
        btnSubmit.disabled = false;
        loader.style.display = "none";
    }
}

window.openEditModal = openEditModal;
window.closeEditModal = closeEditModal;
window.submitPageEdit = submitPageEdit;

// Envia a decisão do usuário (pular ou tentar mais 5 vezes)
async function sendDecision(action) {
    const modal = document.getElementById("pause-modal");
    modal.style.display = "none";
    
    if (!window.activeSessionId) {
        console.error("Nenhum ID de sessão ativo encontrado.");
        return;
    }
    
    const directiveInput = document.getElementById("lider-diretiva");
    let directive = directiveInput ? directiveInput.value.trim() : "";
    
    let finalAction = "retry";
    if (action === "skip") {
        finalAction = "skip";
    } else if (action === "retry_no_directive") {
        finalAction = "retry";
        directive = ""; // Nenhuma diretiva se for apenas retry
    } else if (action === "retry_with_directive") {
        finalAction = "retry";
        if (!directive) {
            alert("Por favor, digite a diretiva de alteração no campo de texto para o Artista.");
            modal.style.display = "flex"; // Reabre o modal
            return;
        }
    }
    
    if (directiveInput) {
        directiveInput.value = ""; // Limpa após enviar
    }
    
    try {
        const res = await fetch("/api/decision", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                session_id: window.activeSessionId,
                action: finalAction,
                directive: directive || null
            })
        });
        
        if (!res.ok) {
            console.error("Erro ao enviar decisão ao servidor:", res.statusText);
        }
    } catch (err) {
        console.error("Erro de rede ao enviar decisão:", err);
    }
}

// Expondo globalmente para uso do onclick em HTML
window.sendDecision = sendDecision;

async function pauseGeneration() {
    if (!window.activeSessionId) return;
    try {
        const res = await fetch("/api/pause", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: window.activeSessionId })
        });
        if (res.ok) {
            document.getElementById("btn-pause").disabled = true;
            document.getElementById("btn-resume").disabled = false;
        }
    } catch (err) {
        console.error("Erro ao pausar:", err);
    }
}

async function resumeGeneration() {
    if (!window.activeSessionId) return;
    try {
        const instrucoes = getCurrentInstructions();
        const res = await fetch("/api/resume", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ 
                session_id: window.activeSessionId,
                instrucoes: instrucoes
            })
        });
        if (res.ok) {
            document.getElementById("btn-pause").disabled = false;
            document.getElementById("btn-resume").disabled = true;
        }
    } catch (err) {
        console.error("Erro ao retomar:", err);
    }
}

async function cancelGeneration() {
    if (!window.activeSessionId) return;
    if (!confirm("Deseja realmente cancelar a geração atual?")) return;
    try {
        const res = await fetch("/api/cancel", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ session_id: window.activeSessionId })
        });
        if (res.ok) {
            document.getElementById("btn-cancel").disabled = true;
            document.getElementById("btn-pause").disabled = true;
            document.getElementById("btn-resume").disabled = true;

            // Para o polling imediatamente
            window.activePolling = false;
            window.activeReader = null;
            
            // Limpa todo o console de mensagens dos agentes
            const consoleMessages = document.getElementById("console-messages");
            if (consoleMessages) {
                consoleMessages.innerHTML = "";
            }
            
            // Reseta a barra de progresso
            const progressBar = document.getElementById("progress-bar");
            if (progressBar) {
                progressBar.style.width = "0%";
            }
            
            // Atualiza status para cancelado
            const statusDot = document.getElementById("status-dot");
            if (statusDot) {
                statusDot.className = "status-dot";
            }
            const statusText = document.getElementById("status-text");
            if (statusText) {
                statusText.innerText = "Cancelado";
            }
            
            // Reabilita o botão principal de geração
            const btnGenerate = document.getElementById("btn-generate");
            if (btnGenerate) {
                btnGenerate.disabled = false;
            }
        }
    } catch (err) {
        console.error("Erro ao cancelar:", err);
    }
}

window.pauseGeneration = pauseGeneration;
window.resumeGeneration = resumeGeneration;
window.cancelGeneration = cancelGeneration;

async function updateBalances(event) {
    if (event) {
        event.stopPropagation();
        event.preventDefault();
    }
    const btn = document.getElementById("btn-refresh-balances");
    const icon = btn ? btn.querySelector(".material-icons-round") : null;
    if (icon) {
        icon.classList.add("animate-spin");
    }

    const geminiKey = document.getElementById("gemini-key") ? document.getElementById("gemini-key").value.trim() : "";
    const openaiKey = document.getElementById("openai-key") ? document.getElementById("openai-key").value.trim() : "";
    const openrouterKey = document.getElementById("openrouter-key") ? document.getElementById("openrouter-key").value.trim() : "";
    const poeKey = document.getElementById("poe-key") ? document.getElementById("poe-key").value.trim() : "";

    try {
        const response = await fetch("/api/balances", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                gemini_api_key: geminiKey || null,
                openai_api_key: openaiKey || null,
                openrouter_api_key: openrouterKey || null,
                poe_api_key: poeKey || null
            })
        });

        if (!response.ok) {
            throw new Error(`Erro: ${response.statusText}`);
        }

        const data = await response.json();
        const list = document.getElementById("balances-list");
        if (list) {
            list.innerHTML = "";
            const keys = ["codex", "poe", "openrouter", "openai", "gemini"];
            const labels = {
                codex: "Codex GPT-Image",
                poe: "Poe API",
                openrouter: "OpenRouter",
                openai: "OpenAI DALL-E 3",
                gemini: "Gemini AI Studio"
            };

            keys.forEach(key => {
                const info = data[key];
                if (!info) return;

                const item = document.createElement("div");
                item.className = "balance-item";

                let valHtml = "";
                if (info.status === "info" && info.url) {
                    valHtml = `<a href="${info.url}" target="_blank">${escapeHtml(info.value)}</a>`;
                } else {
                    valHtml = escapeHtml(info.value);
                }

                item.innerHTML = `
                    <div class="balance-info">
                        <span class="balance-label">${labels[key]}</span>
                        <span class="balance-value" style="color: ${info.color || 'var(--text-primary)'};">${valHtml}</span>
                    </div>
                    <div class="balance-progress-bar">
                        <div class="progress" style="width: ${info.percentage ?? 0}%; background: ${info.color || 'var(--accent)'};"></div>
                    </div>
                `;
                list.appendChild(item);
            });
        }
    } catch (err) {
        console.error("Erro ao atualizar saldos:", err);
        const list = document.getElementById("balances-list");
        if (list) {
            list.innerHTML = `<div style="font-size: 0.8rem; color: var(--color-error); text-align: center; padding: 0.5rem 0;">Falha ao obter saldos.</div>`;
        }
    } finally {
        if (icon) {
            icon.classList.remove("animate-spin");
        }
    }
}

window.updateBalances = updateBalances;
