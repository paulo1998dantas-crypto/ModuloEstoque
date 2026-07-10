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
            const queuedMessage = `${error.message} Job mantido pendente na fila compartilhada para impressao pelo app local.`;
            await postJsonData(`/api/label-jobs/${id}/local-result`, {ok: false, queue_local: true, error: queuedMessage}).catch(() => null);
            updateJobRow(row, "PENDENTE", queuedMessage, "");
            error.queueLocal = true;
            error.queuedMessage = queuedMessage;
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
                if (error.queueLocal) {
                    updateJobRow(row, "PENDENTE", error.queuedMessage || error.message, "");
                } else {
                    updateJobRow(row, "ERRO", error.message, "");
                }
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
                    if (error.queueLocal) {
                        updateJobRow(row, "PENDENTE", error.queuedMessage || error.message, "");
                    } else {
                        updateJobRow(row, "ERRO", error.message, "");
                    }
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
            let data;
            try {
                data = await postJsonData("/api/labels/zpl", {
                    sku: button.dataset.sku,
                    quantidade: qty ? qty.value : 1
                });
                const printed = await localPrintZpl(data.zpl);
                await postJsonData(`/api/label-jobs/${data.job_id}/local-result`, {ok: true});
                if (progress) {
                    const printer = printed?.printer ? ` Fila: ${printed.printer}.` : "";
                    progress.textContent = `Etiqueta enviada para a Zebra neste desktop.${printer}`;
                }
            } catch (error) {
                const queuedMessage = `${error.message} Job mantido pendente na fila compartilhada para impressao pelo app local.`;
                if (data && data.job_id) {
                    await postJsonData(`/api/label-jobs/${data.job_id}/local-result`, {ok: false, queue_local: true, error: queuedMessage}).catch(() => null);
                }
                if (progress) progress.textContent = queuedMessage;
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

function initBackflushBom() {
    const table = document.querySelector("[data-bom-table]");
    if (!table) return;
    const tbody = table.querySelector("tbody");

    function bindRemove(button) {
        button.addEventListener("click", () => {
            const row = button.closest("[data-bom-row]");
            if (row) row.remove();
        });
    }

    table.querySelectorAll("[data-remove-bom-row]").forEach(bindRemove);

    const addButton = document.querySelector("[data-add-bom-row]");
    if (addButton) {
        addButton.addEventListener("click", () => {
            const row = document.createElement("tr");
            row.dataset.bomRow = "1";
            row.innerHTML = [
                '<td><input name="component_sku" required placeholder="COD"></td>',
                '<td class="muted">Item incluido manualmente</td>',
                '<td></td>',
                '<td class="num"></td>',
                '<td><input name="component_quantidade" inputmode="decimal" required placeholder="0"></td>',
                '<td><button class="btn small danger" type="button" data-remove-bom-row>Excluir</button></td>'
            ].join("");
            tbody.appendChild(row);
            bindRemove(row.querySelector("[data-remove-bom-row]"));
            row.querySelector("input").focus();
        });
    }
}

function initCommitmentConsumptionModal() {
    const modal = document.querySelector("[data-commitment-modal]");
    const modalForm = document.querySelector("[data-commitment-modal-form]");
    if (!modal || !modalForm) return;

    const setText = (selector, value) => {
        const target = modal.querySelector(selector);
        if (target) target.textContent = value || "-";
    };

    const close = () => {
        modal.hidden = true;
    };

    document.querySelectorAll("[data-commitment-consumption]").forEach(form => {
        form.addEventListener("submit", event => {
            event.preventDefault();
            const quantityInput = form.querySelector("input[name='quantidade']");
            const requestedQty = quantityInput && quantityInput.value.trim()
                ? quantityInput.value.trim()
                : form.dataset.commitmentPending;

            modalForm.action = form.action;
            const typeInput = modalForm.querySelector("[data-modal-commitment-type]");
            const qtyInput = modalForm.querySelector("[data-modal-commitment-qty]");
            const documentInput = modalForm.querySelector("[data-modal-commitment-consumption-document]");
            const noteInput = modalForm.querySelector("[data-modal-commitment-consumption-note]");

            if (typeInput) typeInput.value = form.querySelector("input[name='tipo']")?.value || "EMPENHO";
            if (qtyInput) qtyInput.value = requestedQty || "";
            if (documentInput) documentInput.value = form.dataset.commitmentDocument || "";
            if (noteInput) noteInput.value = "";

            setText("[data-modal-commitment-id]", `#${form.dataset.commitmentId || ""}`);
            setText("[data-modal-commitment-date]", form.dataset.commitmentDate);
            setText("[data-modal-commitment-sku]", form.dataset.commitmentSku);
            setText("[data-modal-commitment-description]", form.dataset.commitmentDescription);
            setText("[data-modal-commitment-quantity]", form.dataset.commitmentQuantity);
            setText("[data-modal-commitment-pending]", form.dataset.commitmentPending);
            setText("[data-modal-commitment-document]", form.dataset.commitmentDocument);
            setText("[data-modal-commitment-note]", form.dataset.commitmentNote);

            modal.hidden = false;
            if (qtyInput) {
                qtyInput.focus();
                qtyInput.select();
            }
        });
    });

    modal.querySelectorAll("[data-close-commitment-modal]").forEach(button => {
        button.addEventListener("click", close);
    });
    modal.addEventListener("click", event => {
        if (event.target === modal) close();
    });
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && !modal.hidden) close();
    });
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
    initBackflushBom();
    initCommitmentConsumptionModal();
});
