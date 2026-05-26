function focusScanField() {
    const target = document.querySelector("[data-autofocus]") || document.querySelector("[autofocus]");
    if (target) {
        target.focus();
        if (typeof target.select === "function") target.select();
    }
}

function postJson(url) {
    return fetch(url, {
        method: "POST",
        headers: {"X-Requested-With": "fetch"}
    }).then(async response => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
            throw new Error(data.error || "Falha na operacao.");
        }
        return data;
    });
}

function postJsonData(url, payload) {
    return fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Requested-With": "fetch"
        },
        body: JSON.stringify(payload || {})
    }).then(async response => {
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
            throw new Error(data.error || "Falha na operacao.");
        }
        return data;
    });
}

function printMode() {
    return document.body.dataset.printMode || "server";
}

async function localPrintZpl(zpl) {
    const bridge = document.body.dataset.localPrintBridge || "http://127.0.0.1:5000";
    let response;
    try {
        response = await fetch(`${bridge}/api/local-print-zpl`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Requested-With": "fetch"
            },
            body: JSON.stringify({zpl})
        });
    } catch (error) {
        throw new Error("Ponte local nao encontrada. Abra o app local ou o .exe neste desktop e tente novamente.");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
        throw new Error(data.error || "Falha ao imprimir pela ponte local.");
    }
    return data;
}

async function printJob(row) {
    const id = row.dataset.jobId;
    if (printMode() === "bridge") {
        const data = await postJson(`/api/label-jobs/${id}/zpl`);
        try {
            const printed = await localPrintZpl(data.zpl);
            const result = await postJsonData(`/api/label-jobs/${id}/local-result`, {ok: true});
            updateJobRow(row, result.status || "IMPRESSO", "", result.printed_at);
            return printed;
        } catch (error) {
            await postJsonData(`/api/label-jobs/${id}/local-result`, {ok: false, error: error.message}).catch(() => null);
            updateJobRow(row, "ERRO", error.message, "");
            throw error;
        }
    }
    const data = await postJson(`/api/label-jobs/${id}/print`);
    updateJobRow(row, data.status || "IMPRESSO", "", data.printed_at);
    return data;
}

function updateJobRow(row, status, error, printedAt) {
    row.dataset.jobStatus = status;
    const badge = row.querySelector("[data-job-badge]");
    if (badge) {
        badge.textContent = status;
        badge.className = "badge " + status.toLowerCase();
    }
    const errorCell = row.querySelector("[data-job-error]");
    if (errorCell) errorCell.textContent = error || "";
    const printedCell = row.querySelector("[data-printed-at]");
    if (printedCell && printedAt) printedCell.textContent = printedAt;
}

function initPrintQueue() {
    const progress = document.querySelector("[data-print-progress]");

    document.querySelectorAll("[data-print-job]").forEach(button => {
        button.addEventListener("click", async () => {
            const row = button.closest("[data-job-row]");
            const id = button.dataset.printJob;
            if (progress) progress.textContent = `Imprimindo job ${id}...`;
            try {
                await printJob(row);
                if (progress) progress.textContent = `Job ${id} impresso.`;
            } catch (error) {
                updateJobRow(row, "ERRO", error.message, "");
                if (progress) progress.textContent = error.message;
            }
        });
    });

    document.querySelectorAll("[data-save-job]").forEach(button => {
        button.addEventListener("click", async () => {
            const id = button.dataset.saveJob;
            try {
                const data = await postJson(`/api/label-jobs/${id}/save-zpl`);
                if (progress) progress.textContent = `ZPL salvo: ${data.path}`;
                if (data.download_url) window.location.href = data.download_url;
            } catch (error) {
                if (progress) progress.textContent = error.message;
            }
        });
    });

    document.querySelectorAll("[data-mark-job]").forEach(button => {
        button.addEventListener("click", async () => {
            const row = button.closest("[data-job-row]");
            const id = button.dataset.markJob;
            try {
                const data = await postJson(`/api/label-jobs/${id}/mark-printed`);
                updateJobRow(row, data.status || "IMPRESSO", "", "");
            } catch (error) {
                if (progress) progress.textContent = error.message;
            }
        });
    });

    document.querySelectorAll("[data-cancel-job]").forEach(button => {
        button.addEventListener("click", async () => {
            const row = button.closest("[data-job-row]");
            const id = button.dataset.cancelJob;
            try {
                const data = await postJson(`/api/label-jobs/${id}/cancel`);
                updateJobRow(row, data.status || "CANCELADO", "", "");
            } catch (error) {
                if (progress) progress.textContent = error.message;
            }
        });
    });

    const printAll = document.querySelector("[data-print-all]");
    if (printAll) {
        printAll.addEventListener("click", async () => {
            const rows = [...document.querySelectorAll("[data-job-row]")]
                .filter(row => ["PENDENTE", "ERRO"].includes(row.dataset.jobStatus));
            if (!rows.length) {
                if (progress) progress.textContent = "Nao ha jobs pendentes.";
                return;
            }
            printAll.disabled = true;
            for (let index = 0; index < rows.length; index += 1) {
                const row = rows[index];
                const id = row.dataset.jobId;
                if (progress) progress.textContent = `Imprimindo ${index + 1}/${rows.length} - job ${id}`;
                try {
                    await printJob(row);
                } catch (error) {
                    updateJobRow(row, "ERRO", error.message, "");
                }
            }
            printAll.disabled = false;
            if (progress) progress.textContent = "Processamento da fila concluido.";
        });
    }
}

function initSingleLocalPrint() {
    document.querySelectorAll("[data-local-print-label]").forEach(button => {
        button.addEventListener("click", async () => {
            const form = button.closest("form");
            const progress = form ? form.querySelector("[data-local-print-progress]") : document.querySelector("[data-local-print-progress]");
            const qty = form ? form.querySelector("input[name='quantidade']") : null;
            button.disabled = true;
            if (progress) progress.textContent = "Gerando ZPL e enviando para a ponte local...";
            try {
                const data = await postJsonData("/api/labels/zpl", {
                    sku: button.dataset.sku,
                    quantidade: qty ? qty.value : 1
                });
                await localPrintZpl(data.zpl);
                await postJsonData(`/api/label-jobs/${data.job_id}/local-result`, {ok: true});
                if (progress) progress.textContent = "Etiqueta enviada para a Zebra neste desktop.";
            } catch (error) {
                if (progress) progress.textContent = error.message;
            } finally {
                button.disabled = false;
            }
        });
    });
}

function parseDecimal(value) {
    const normalized = String(value || "").replace(",", ".").trim();
    const parsed = Number.parseFloat(normalized);
    return Number.isFinite(parsed) ? parsed : 0;
}

function formatDecimal(value) {
    return value.toLocaleString("pt-BR", {
        minimumFractionDigits: 0,
        maximumFractionDigits: 3
    });
}

function initInventoryDiff() {
    const counted = document.querySelector("[data-counted-stock]");
    const current = document.querySelector("[data-current-stock]");
    const target = document.querySelector("[data-inventory-diff]");
    if (!counted || !current || !target) return;

    const update = () => {
        const diff = parseDecimal(counted.value) - parseDecimal(current.textContent);
        target.textContent = formatDecimal(diff);
        target.classList.toggle("negative", diff < 0);
        target.classList.toggle("positive", diff > 0);
    };

    counted.addEventListener("input", update);
    update();
}

document.addEventListener("DOMContentLoaded", () => {
    focusScanField();

    const navToggle = document.querySelector("[data-nav-toggle]");
    const nav = document.querySelector("[data-nav]");
    if (navToggle && nav) {
        navToggle.addEventListener("click", () => nav.classList.toggle("open"));
    }

    document.querySelectorAll(".confirmable").forEach(form => {
        form.addEventListener("submit", event => {
            const message = form.dataset.confirm || "Confirmar operacao?";
            if (!window.confirm(message)) event.preventDefault();
        });
    });

    initPrintQueue();
    initSingleLocalPrint();
    initInventoryDiff();
});
