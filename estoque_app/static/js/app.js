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
                const data = await postJson(`/api/label-jobs/${id}/print`);
                updateJobRow(row, data.status || "IMPRESSO", "", data.printed_at);
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
                    const data = await postJson(`/api/label-jobs/${id}/print`);
                    updateJobRow(row, data.status || "IMPRESSO", "", data.printed_at);
                } catch (error) {
                    updateJobRow(row, "ERRO", error.message, "");
                }
            }
            printAll.disabled = false;
            if (progress) progress.textContent = "Processamento da fila concluido.";
        });
    }
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
});
