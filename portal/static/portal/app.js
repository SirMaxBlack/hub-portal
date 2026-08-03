const AUTH_METHOD_ACCOUNT = "account";
const AUTH_METHOD_TOKEN = "token";
const THEME_KEY = "hubPortalTheme";
const RELEASE_RELOAD_KEY = "hubPortalReloadReleaseId";
const RELEASE_CHECK_INTERVAL_MS = 15000;
const SERVICE_STATUS_POLL_INTERVAL_MS = 15000;
const STATUS_LABELS = {
    up: "Сервис доступен",
    down: "Сервис временно недоступен",
    unknown: "Статус неизвестен, сервис ещё проверяется",
};
let serviceStatusPollTimer = null;
let serviceStatusPollInFlight = false;

function closeAllEnhancedSelects(exceptWrapper = null) {
    document.querySelectorAll("[data-enhanced-select-root].is-open").forEach((wrapper) => {
        if (exceptWrapper && wrapper === exceptWrapper) {
            return;
        }
        wrapper.__closeEnhancedSelectPanel?.();
    });
}

function closeAllActionMenus(exceptMenu = null) {
    document.querySelectorAll("[data-action-menu].is-open").forEach((menu) => {
        if (exceptMenu && menu === exceptMenu) {
            return;
        }
        menu.__setActionMenuOpenState?.(false);
    });
}

function getStoredTheme() {
    return localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
}

function getCookie(name) {
    const cookieValue = document.cookie
        .split(";")
        .map((chunk) => chunk.trim())
        .find((chunk) => chunk.startsWith(name + "="));
    return cookieValue ? decodeURIComponent(cookieValue.split("=").slice(1).join("=")) : "";
}

function showToast(message, type = "success") {
    const stack = document.querySelector("[data-toast-stack]");
    if (!stack || !message) {
        return;
    }

    const toast = document.createElement("div");
    toast.className = "toast " + (type === "error" ? "is-error" : "is-success");
    toast.textContent = message;
    stack.appendChild(toast);

    window.setTimeout(() => {
        toast.remove();
    }, 3400);
}

function replaceServiceLayoutBoardMarkup(boardHtml) {
    if (!boardHtml) {
        return null;
    }

    const currentBoard = document.querySelector("[data-service-layout-board]");
    if (!currentBoard) {
        return null;
    }

    const wrapper = document.createElement("div");
    wrapper.innerHTML = boardHtml.trim();
    const replacement = wrapper.querySelector("[data-service-layout-board]");
    if (!replacement) {
        return null;
    }

    currentBoard.replaceWith(replacement);
    initializeInteractiveUi(document, {
        secretModal: window.__hubSecretModal,
        appModal: window.__hubAppModal,
    });
    return replacement;
}

function applyTheme(theme) {
    const root = document.documentElement;
    const isLight = theme === "light";
    if (isLight) {
        root.setAttribute("data-theme", "light");
    } else {
        root.removeAttribute("data-theme");
    }
    localStorage.setItem(THEME_KEY, theme);

    document.querySelectorAll("[data-theme-icon]").forEach((node) => {
        node.textContent = isLight ? "☾" : "☀";
    });
    document.querySelectorAll("[data-theme-label]").forEach((node) => {
        node.textContent = isLight ? "День" : "Ночь";
    });
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
        button.setAttribute("title", isLight ? "Включить ночной режим" : "Включить дневной режим");
        button.setAttribute("aria-label", isLight ? "Включить ночной режим" : "Включить дневной режим");
    });
}

function setupThemeToggle() {
    applyTheme(getStoredTheme());
    if (document.documentElement.dataset.themeToggleReady === "1") {
        return;
    }
    document.documentElement.dataset.themeToggleReady = "1";
    document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-theme-toggle]");
        if (!button) {
            return;
        }
        const nextTheme = document.documentElement.getAttribute("data-theme") === "light" ? "dark" : "light";
        applyTheme(nextTheme);
    });
}

function setupReleaseWatcher() {
    const body = document.body;
    const currentReleaseId = body?.dataset.releaseId?.trim();
    const releaseStateUrl = body?.dataset.releaseStateUrl?.trim();
    if (!body || !currentReleaseId || !releaseStateUrl) {
        return;
    }
    if (document.documentElement.dataset.releaseWatcherReady === "1") {
        return;
    }
    document.documentElement.dataset.releaseWatcherReady = "1";

    let requestInFlight = false;

    async function checkRelease(force = false) {
        if (requestInFlight || (!force && document.hidden)) {
            return;
        }
        requestInFlight = true;
        try {
            const payload = await fetchJson(releaseStateUrl, {
                method: "GET",
                cache: "no-store",
                headers: {
                    "Cache-Control": "no-cache",
                    Pragma: "no-cache",
                },
            });
            const nextReleaseId = String(payload.release_id || "").trim();
            if (!nextReleaseId || nextReleaseId === currentReleaseId) {
                return;
            }
            if (window.sessionStorage.getItem(RELEASE_RELOAD_KEY) === nextReleaseId) {
                return;
            }
            window.sessionStorage.setItem(RELEASE_RELOAD_KEY, nextReleaseId);
            window.location.reload();
        } catch (error) {
            // Silent by design: deploy transitions must not break page interactions.
        } finally {
            requestInFlight = false;
        }
    }

    window.setTimeout(() => checkRelease(true), 1200);
    window.setInterval(checkRelease, RELEASE_CHECK_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            checkRelease(true);
        }
    });
    window.addEventListener("pageshow", () => checkRelease(true));
}

function setupAuthSwitch(root = document) {
    const form = root.querySelector("[data-auth-form]");
    const switchRoot = root.querySelector("[data-auth-switch]");
    const stage = form?.querySelector("[data-auth-panel-stage]");
    if (!form || !switchRoot) {
        return;
    }

    const hiddenInput = form.querySelector('input[name="auth_method"]');
    const buttons = switchRoot.querySelectorAll("[data-auth-mode-button]");
    const panels = form.querySelectorAll("[data-auth-panel]");
    const modeOrder = [AUTH_METHOD_ACCOUNT, AUTH_METHOD_TOKEN];
    let currentMode = hiddenInput?.value || AUTH_METHOD_ACCOUNT;

    function getFocusableTarget(panel) {
        return panel.querySelector('input:not([type="hidden"]), textarea, select, button:not([disabled])');
    }

    function getPanelByMode(mode) {
        return Array.from(panels).find((panel) => panel.dataset.authPanel === mode) || null;
    }

    function getModeIndex(mode) {
        return modeOrder.indexOf(mode);
    }

    function syncStageHeight(activeMode = currentMode, { immediate = false } = {}) {
        if (!stage) {
            return;
        }
        const activePanel = getPanelByMode(activeMode);
        if (!activePanel) {
            return;
        }
        if (immediate) {
            stage.classList.add("is-static");
        }
        stage.style.height = `${activePanel.scrollHeight}px`;
        if (immediate) {
            stage.offsetHeight;
            stage.classList.remove("is-static");
        }
    }

    function placePanels(activeMode) {
        const activeIndex = getModeIndex(activeMode);
        panels.forEach((panel) => {
            const isActive = panel.dataset.authPanel === activeMode;
            const panelIndex = getModeIndex(panel.dataset.authPanel);
            panel.hidden = false;
            panel.classList.remove("is-active", "is-before", "is-after");
            if (isActive) {
                panel.classList.add("is-active");
            } else if (panelIndex < activeIndex) {
                panel.classList.add("is-before");
            } else {
                panel.classList.add("is-after");
            }
            panel.setAttribute("aria-hidden", isActive ? "false" : "true");
            panel.inert = !isActive;
        });
    }

    function setMode(mode, { focus = false, immediate = false } = {}) {
        const previousMode = currentMode;
        currentMode = mode;
        hiddenInput.value = mode;
        buttons.forEach((button) => {
            button.classList.toggle("is-active", button.dataset.authModeButton === mode);
            button.setAttribute("aria-pressed", button.dataset.authModeButton === mode ? "true" : "false");
        });
        placePanels(mode);
        syncStageHeight(previousMode, { immediate });
        if (!immediate) {
            window.requestAnimationFrame(() => syncStageHeight(mode));
        } else {
            syncStageHeight(mode, { immediate: true });
        }
        form.dispatchEvent(new CustomEvent("hub:auth-mode-changed", { detail: { mode } }));
        if (focus) {
            const activePanel = getPanelByMode(mode);
            const nextField = activePanel ? getFocusableTarget(activePanel) : null;
            if (nextField) {
                window.setTimeout(() => nextField.focus(), immediate ? 0 : 120);
            }
        }
    }

    buttons.forEach((button) => {
        if (button.dataset.boundAuthMode) {
            return;
        }
        button.dataset.boundAuthMode = "1";
        button.addEventListener("click", () => {
            const mode = button.dataset.authModeButton;
            if (mode === currentMode) {
                return;
            }
            setMode(mode, { focus: true });
        });
    });

    if (stage) {
        stage.classList.add("is-enhanced");
    }
    panels.forEach((panel) => {
        panel.hidden = false;
    });
    setMode(currentMode, { focus: false, immediate: true });
    window.addEventListener("resize", () => syncStageHeight(currentMode, { immediate: true }));
}

function setupPasswordVisibility(root = document) {
    root.querySelectorAll("[data-password-visibility-toggle]").forEach((button) => {
        if (button.dataset.boundVisibility) {
            return;
        }
        button.dataset.boundVisibility = "1";
        button.addEventListener("click", () => {
            const target = root.querySelector("#" + button.dataset.passwordTarget) || document.getElementById(button.dataset.passwordTarget);
            if (!target) {
                return;
            }
            const nextType = target.type === "password" ? "text" : "password";
            const label = button.dataset.visibilityLabel || "значение";
            target.type = nextType;
            const isVisible = nextType === "text";
            button.classList.toggle("is-active", isVisible);
            button.setAttribute("aria-pressed", isVisible ? "true" : "false");
            button.setAttribute("aria-label", isVisible ? `Скрыть ${label}` : `Показать ${label}`);
            button.setAttribute("title", isVisible ? `Скрыть ${label}` : `Показать ${label}`);
        });
    });
}

function setupAuthForm(root = document) {
    const form = root.querySelector("[data-auth-form]");
    if (!form || form.dataset.boundAuthForm) {
        return;
    }
    form.dataset.boundAuthForm = "1";

    const passwordField = form.querySelector("#id_password");
    const usernameField = form.querySelector("#id_username");
    const tokenField = form.querySelector("#id_token");
    const authMethodField = form.querySelector('input[name="auth_method"]');
    const warning = form.querySelector("[data-caps-lock-warning]");
    const submitButton = form.querySelector("[data-auth-submit-button]");
    const spinner = form.querySelector("[data-submit-spinner]");
    const submitLabel = form.querySelector("[data-submit-label]");

    function getCurrentMode() {
        return authMethodField?.value === AUTH_METHOD_TOKEN ? AUTH_METHOD_TOKEN : AUTH_METHOD_ACCOUNT;
    }

    function hasAccountCredentials() {
        return Boolean(usernameField?.value?.trim() && passwordField?.value);
    }

    function hasTokenCredential() {
        return Boolean(tokenField?.value?.trim());
    }

    function syncSubmitState() {
        if (!submitButton) {
            return;
        }
        const mode = getCurrentMode();
        const isReady = mode === AUTH_METHOD_TOKEN ? hasTokenCredential() : hasAccountCredentials();
        submitButton.disabled = !isReady;
    }

    if (passwordField && warning) {
        passwordField.addEventListener("keydown", (event) => {
            warning.hidden = !event.getModifierState("CapsLock");
        });
        passwordField.addEventListener("keyup", (event) => {
            warning.hidden = !event.getModifierState("CapsLock");
        });
        passwordField.addEventListener("blur", () => {
            warning.hidden = true;
        });
    }

    [usernameField, passwordField, tokenField].forEach((field) => {
        if (!field) {
            return;
        }
        field.addEventListener("input", syncSubmitState);
        field.addEventListener("change", syncSubmitState);
    });

    form.addEventListener("hub:auth-mode-changed", syncSubmitState);

    form.addEventListener("submit", () => {
        if (!submitButton) {
            return;
        }
        submitButton.disabled = true;
        if (spinner) {
            spinner.hidden = false;
        }
        if (submitLabel) {
            submitLabel.textContent = "Выполняется вход";
        }
    });

    syncSubmitState();
}

function createFocusTrap(container) {
    function onKeydown(event) {
        if (event.key !== "Tab") {
            return;
        }
        const focusable = container.querySelectorAll(
            'a[href], button:not([disabled]), textarea, input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
        );
        if (!focusable.length) {
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }
    container.addEventListener("keydown", onKeydown);
    return () => container.removeEventListener("keydown", onKeydown);
}

function setupMobileNav() {
    const shell = document.querySelector("[data-mobile-nav]");
    const opener = document.querySelector("[data-mobile-nav-toggle]");
    if (!shell || !opener) {
        return;
    }

    const panel = shell.querySelector(".drawer-panel");
    let restoreFocus = null;
    let releaseFocusTrap = null;

    function close(force = false) {
        const activeForm = shell.querySelector("form[data-modal-form]");
        if (!force && activeForm && activeForm.dataset.dirty === "1" && !window.confirm("Закрыть без сохранения изменений?")) {
            return;
        }
        shell.hidden = true;
        document.body.classList.remove("is-scroll-locked");
        if (releaseFocusTrap) {
            releaseFocusTrap();
            releaseFocusTrap = null;
        }
        if (restoreFocus) {
            restoreFocus.focus();
            restoreFocus = null;
        }
    }

    function open() {
        restoreFocus = document.activeElement;
        shell.hidden = false;
        document.body.classList.add("is-scroll-locked");
        if (releaseFocusTrap) {
            releaseFocusTrap();
        }
        releaseFocusTrap = createFocusTrap(panel);
        const firstButton = shell.querySelector("a, button");
        if (firstButton) {
            firstButton.focus();
        }
    }

    opener.addEventListener("click", open);
    shell.querySelectorAll("[data-mobile-nav-dismiss]").forEach((button) => {
        button.addEventListener("click", () => close());
    });
    shell.querySelectorAll("a").forEach((link) => {
        link.addEventListener("click", () => close(true));
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !shell.hidden) {
            close();
        }
    });
}

function setupSecretModal() {
    const shell = document.querySelector("[data-secret-modal]");
    if (!shell) {
        return { open() {}, close() {} };
    }

    const titleNode = shell.querySelector("[data-secret-modal-title]");
    const eyebrowNode = shell.querySelector("[data-secret-modal-eyebrow]");
    const subtitleNode = shell.querySelector("[data-secret-modal-subtitle]");
    const valueNode = shell.querySelector("[data-secret-modal-value]");
    const metaNode = shell.querySelector("[data-secret-modal-meta]");
    const copyButton = shell.querySelector("[data-secret-copy-button]");
    let currentValue = "";
    let restoreFocus = null;
    let releaseFocusTrap = null;

    function close() {
        shell.hidden = true;
        document.body.classList.remove("is-scroll-locked");
        currentValue = "";
        if (releaseFocusTrap) {
            releaseFocusTrap();
            releaseFocusTrap = null;
        }
        if (restoreFocus) {
            restoreFocus.focus();
            restoreFocus = null;
        }
    }

    function open(config) {
        restoreFocus = document.activeElement;
        currentValue = config.value || "";
        eyebrowNode.textContent = config.eyebrow || "Секрет";
        titleNode.textContent = config.title || "Секрет";
        if (config.subtitle) {
            subtitleNode.hidden = false;
            subtitleNode.textContent = config.subtitle;
        } else {
            subtitleNode.hidden = true;
            subtitleNode.textContent = "";
        }
        valueNode.textContent = currentValue || "—";
        metaNode.innerHTML = "";
        (config.meta || []).forEach((item) => {
            const row = document.createElement("div");
            row.className = "detail-row";
            row.innerHTML = "<dt></dt><dd></dd>";
            row.querySelector("dt").textContent = item.label;
            row.querySelector("dd").textContent = item.value || "—";
            metaNode.appendChild(row);
        });
        shell.hidden = false;
        document.body.classList.add("is-scroll-locked");
        if (releaseFocusTrap) {
            releaseFocusTrap();
        }
        releaseFocusTrap = createFocusTrap(shell.querySelector(".modal-card"));
        copyButton.focus();
    }

    shell.querySelectorAll("[data-secret-modal-dismiss]").forEach((button) => {
        button.addEventListener("click", close);
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !shell.hidden) {
            close();
        }
    });
    copyButton.addEventListener("click", async () => {
        if (!currentValue) {
            return;
        }
        try {
            await navigator.clipboard.writeText(currentValue);
            showToast("Значение скопировано.", "success");
        } catch (error) {
            showToast("Не удалось скопировать значение.", "error");
        }
    });

    return { open, close };
}

function setupServiceOptionModal() {
    const shell = document.querySelector("[data-service-option-modal]");
    if (!shell) {
        return { open() {}, close() {} };
    }

    const titleNode = shell.querySelector("[data-service-option-modal-title]");
    const eyebrowNode = shell.querySelector("[data-service-option-modal-eyebrow]");
    const subtitleNode = shell.querySelector("[data-service-option-modal-subtitle]");
    const summaryNode = shell.querySelector("[data-service-option-modal-summary]");
    const metaNode = shell.querySelector("[data-service-option-modal-meta]");
    const sectionsNode = shell.querySelector("[data-service-option-modal-sections]");
    let restoreFocus = null;
    let releaseFocusTrap = null;

    function close() {
        shell.hidden = true;
        document.body.classList.remove("is-scroll-locked");
        metaNode.innerHTML = "";
        sectionsNode.innerHTML = "";
        if (releaseFocusTrap) {
            releaseFocusTrap();
            releaseFocusTrap = null;
        }
        if (restoreFocus) {
            restoreFocus.focus();
            restoreFocus = null;
        }
    }

    function appendMeta(label, value) {
        if (!value) {
            return;
        }
        const row = document.createElement("div");
        row.className = "detail-row";
        row.innerHTML = "<dt></dt><dd></dd>";
        row.querySelector("dt").textContent = label;
        row.querySelector("dd").textContent = value;
        metaNode.appendChild(row);
    }

    function appendTextSection(title, value) {
        if (!value) {
            return;
        }
        const section = document.createElement("section");
        section.className = "option-doc-section";
        const heading = document.createElement("h3");
        heading.textContent = title;
        const paragraph = document.createElement("p");
        paragraph.textContent = value;
        section.append(heading, paragraph);
        sectionsNode.appendChild(section);
    }

    function appendListSection(title, values, extraClass = "") {
        if (!Array.isArray(values) || !values.length) {
            return;
        }
        const section = document.createElement("section");
        section.className = `option-doc-section ${extraClass}`.trim();
        const heading = document.createElement("h3");
        heading.textContent = title;
        const list = document.createElement("ul");
        list.className = "option-doc-list";
        values.forEach((item) => {
            const listItem = document.createElement("li");
            listItem.textContent = item;
            list.appendChild(listItem);
        });
        section.append(heading, list);
        sectionsNode.appendChild(section);
    }

    function appendCodeSection(title, values) {
        if (!Array.isArray(values) || !values.length) {
            return;
        }
        const section = document.createElement("section");
        section.className = "option-doc-section";
        const heading = document.createElement("h3");
        heading.textContent = title;
        const pre = document.createElement("pre");
        const code = document.createElement("code");
        pre.className = "option-doc-code";
        code.textContent = values.join("\n");
        pre.appendChild(code);
        section.append(heading, pre);
        sectionsNode.appendChild(section);
    }

    function open(option, opener = document.activeElement) {
        restoreFocus = opener;
        eyebrowNode.textContent = `${option.category || "Опция nginx"} · MAN`;
        titleNode.textContent = option.title || "Опция nginx";
        if (option.description) {
            subtitleNode.hidden = false;
            subtitleNode.textContent = option.description;
        } else {
            subtitleNode.hidden = true;
            subtitleNode.textContent = "";
        }
        summaryNode.textContent = option.summary || option.description || "";
        metaNode.innerHTML = "";
        sectionsNode.innerHTML = "";

        appendMeta("Категория", option.category || "—");
        appendMeta("Контекст", option.contexts || "—");
        appendMeta("Рекомендация", option.recommendation_label || "—");
        appendMeta(
            "Режим",
            option.locked ? (option.auto_when_path_prefix ? "Авто-системный" : "Системный") : "Ручной выбор"
        );
        appendMeta("Подсказка", option.recommendation_note || "");

        appendTextSection("Когда включать", option.when_to_use || "");
        appendTextSection("Когда не включать", option.when_not_to_use || "");
        appendListSection("Автоматические связи", option.dependencies_titles || []);
        appendListSection("Конфликты", option.conflicts_titles || []);
        appendListSection("Практика применения", option.practices || []);
        appendListSection("Примеры", option.examples || []);
        appendCodeSection("Что попадёт в nginx", option.directives || []);

        shell.hidden = false;
        document.body.classList.add("is-scroll-locked");
        if (releaseFocusTrap) {
            releaseFocusTrap();
        }
        releaseFocusTrap = createFocusTrap(shell.querySelector(".modal-card"));
        window.setTimeout(() => shell.querySelector("[data-service-option-modal-dismiss]")?.focus(), 20);
    }

    shell.querySelectorAll("[data-service-option-modal-dismiss]").forEach((button) => {
        button.addEventListener("click", close);
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !shell.hidden) {
            close();
        }
    });

    return { open, close };
}

function setupAuditClearDialog() {
    const shell = document.querySelector("[data-audit-clear-modal]");
    if (!shell || shell.dataset.boundAuditClearDialog === "1") {
        return;
    }
    shell.dataset.boundAuditClearDialog = "1";

    const form = shell.querySelector("[data-audit-clear-form]");
    const input = shell.querySelector("[data-audit-clear-input]");
    const submitButton = shell.querySelector("[data-audit-clear-submit]");
    const confirmationPhrase = "ОЧИСТИТЬ";
    let restoreFocus = null;
    let releaseFocusTrap = null;

    function syncSubmitState() {
        if (!submitButton || !input) {
            return;
        }
        submitButton.disabled = input.value !== confirmationPhrase;
    }

    function resetState() {
        if (input) {
            input.value = "";
        }
        syncSubmitState();
    }

    function close(force = false) {
        if (!force && form?.dataset.submitting === "1") {
            return;
        }
        shell.hidden = true;
        document.body.classList.remove("is-scroll-locked");
        resetState();
        if (releaseFocusTrap) {
            releaseFocusTrap();
            releaseFocusTrap = null;
        }
        if (restoreFocus) {
            restoreFocus.focus();
            restoreFocus = null;
        }
    }

    function open(opener = document.activeElement) {
        restoreFocus = opener;
        shell.hidden = false;
        document.body.classList.add("is-scroll-locked");
        syncSubmitState();
        if (releaseFocusTrap) {
            releaseFocusTrap();
        }
        releaseFocusTrap = createFocusTrap(shell.querySelector(".modal-card"));
        window.setTimeout(() => input?.focus(), 20);
    }

    document.querySelectorAll("[data-open-audit-clear-modal]").forEach((button) => {
        if (button.dataset.boundAuditClearOpener) {
            return;
        }
        button.dataset.boundAuditClearOpener = "1";
        button.addEventListener("click", () => open(button));
    });

    shell.querySelectorAll("[data-audit-clear-dismiss]").forEach((button) => {
        button.addEventListener("click", () => close());
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !shell.hidden) {
            close();
        }
    });
    input?.addEventListener("input", syncSubmitState);

    form?.addEventListener("submit", async (event) => {
        event.preventDefault();
        syncSubmitState();
        if (!submitButton || submitButton.disabled) {
            return;
        }
        form.dataset.submitting = "1";
        submitButton.disabled = true;
        try {
            const payload = await fetchJson(form.action, {
                method: "POST",
                body: new FormData(form),
            });
            close(true);
            showToast(payload.message || "Журнал очищен.", "success");
            window.location.href = payload.redirect_url || "/rights/logs/";
        } catch (payload) {
            syncSubmitState();
            showToast(payload?.message || "Не удалось очистить журнал.", "error");
        } finally {
            form.dataset.submitting = "";
        }
    });
}

function setupEnhancedSelects(root = document) {
    if (document.documentElement.dataset.enhancedSelectGlobalsReady !== "1") {
        document.documentElement.dataset.enhancedSelectGlobalsReady = "1";
        document.addEventListener("click", (event) => {
            if (!event.target.closest("[data-enhanced-select-root]")) {
                closeAllEnhancedSelects();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closeAllEnhancedSelects();
            }
        });
        window.addEventListener("resize", () => {
            closeAllEnhancedSelects();
        });
    }

    root.querySelectorAll("select[data-enhanced-select]").forEach((select) => {
        if (select.dataset.enhancedReady) {
            return;
        }
        select.dataset.enhancedReady = "1";
        select.classList.add("enhanced-select-native");

        const wrapper = document.createElement("div");
        wrapper.className = "enhanced-select";
        wrapper.dataset.enhancedSelectRoot = "1";

        const trigger = document.createElement("button");
        trigger.type = "button";
        trigger.className = "enhanced-select-trigger";
        trigger.innerHTML = '<span class="enhanced-select-value"></span><span aria-hidden="true">▾</span>';

        const panel = document.createElement("div");
        panel.className = "enhanced-select-panel";
        panel.hidden = true;

        let searchInput = null;
        if (select.dataset.enhancedSearch === "true") {
            searchInput = document.createElement("input");
            searchInput.type = "text";
            searchInput.className = "enhanced-select-search";
            searchInput.placeholder = "Поиск";
            panel.appendChild(searchInput);
        }

        const optionsWrap = document.createElement("div");
        optionsWrap.className = "enhanced-select-options";
        panel.appendChild(optionsWrap);

        function getSelectedOption() {
            return select.options[select.selectedIndex];
        }

        function closePanel() {
            panel.hidden = true;
            wrapper.classList.remove("is-open");
            wrapper.classList.remove("is-open-up");
            if (searchInput) {
                searchInput.value = "";
                filterOptions("");
            }
        }

        function positionPanel() {
            const viewportPadding = 16;
            const rect = wrapper.getBoundingClientRect();
            const availableBelow = window.innerHeight - rect.bottom - viewportPadding;
            const availableAbove = rect.top - viewportPadding;
            const openUp = availableBelow < 240 && availableAbove > availableBelow;
            const usableSpace = Math.max(openUp ? availableAbove : availableBelow, 220);

            wrapper.classList.toggle("is-open-up", openUp);
            panel.style.setProperty("--enhanced-select-max-height", `${Math.min(usableSpace, 360)}px`);
        }

        function openPanel() {
            closeAllEnhancedSelects(wrapper);
            positionPanel();
            panel.hidden = false;
            wrapper.classList.add("is-open");
            if (searchInput) {
                searchInput.focus();
            }
        }

        function syncTrigger() {
            const option = getSelectedOption();
            trigger.querySelector(".enhanced-select-value").textContent = option ? option.textContent : "Выберите";
            optionsWrap.querySelectorAll(".enhanced-select-option").forEach((button) => {
                button.classList.toggle("is-selected", button.dataset.value === select.value);
            });
        }

        function renderOptions() {
            optionsWrap.innerHTML = "";
            Array.from(select.options).forEach((option) => {
                const optionButton = document.createElement("button");
                optionButton.type = "button";
                optionButton.className = "enhanced-select-option";
                optionButton.textContent = option.textContent;
                optionButton.dataset.value = option.value;
                optionButton.addEventListener("click", () => {
                    select.value = option.value;
                    select.dispatchEvent(new Event("change", { bubbles: true }));
                    syncTrigger();
                    closePanel();
                });
                optionsWrap.appendChild(optionButton);
            });
            syncTrigger();
        }

        function filterOptions(term) {
            const normalized = term.trim().toLowerCase();
            optionsWrap.querySelectorAll(".enhanced-select-option").forEach((button) => {
                button.hidden = normalized && !button.textContent.toLowerCase().includes(normalized);
            });
        }

        wrapper.__closeEnhancedSelectPanel = closePanel;

        trigger.addEventListener("click", (event) => {
            event.stopPropagation();
            if (panel.hidden) {
                openPanel();
            } else {
                closePanel();
            }
        });
        if (searchInput) {
            searchInput.addEventListener("input", () => filterOptions(searchInput.value));
        }
        select.addEventListener("change", () => {
            syncTrigger();
            if (select.name === "period") {
                togglePeriodCustomFields();
            }
        });

        select.insertAdjacentElement("afterend", wrapper);
        wrapper.appendChild(trigger);
        wrapper.appendChild(panel);
        renderOptions();
    });
}

function togglePeriodCustomFields() {
    const periodSelect = document.querySelector('select[name="period"]');
    if (!periodSelect) {
        return;
    }
    const isCustom = periodSelect.value === "custom";
    document.querySelectorAll("[data-period-custom-fields]").forEach((node) => {
        node.classList.toggle("is-hidden", !isCustom);
    });
}

function setupTabs(root = document) {
    root.querySelectorAll("[data-tab-strip]").forEach((strip) => {
        if (strip.dataset.tabsReady) {
            return;
        }
        strip.dataset.tabsReady = "1";
        const container = strip.closest("[data-user-modal-shell]") || root;
        const buttons = strip.querySelectorAll("[data-tab-button]");
        const panels = container.querySelectorAll("[data-tab-panel]");

        function activate(name) {
            buttons.forEach((button) => {
                button.classList.toggle("is-active", button.dataset.tabButton === name);
            });
            panels.forEach((panel) => {
                panel.classList.toggle("is-active", panel.dataset.tabPanel === name);
            });
        }

        buttons.forEach((button) => {
            button.addEventListener("click", () => activate(button.dataset.tabButton));
        });
        container.querySelectorAll("[data-tab-target]").forEach((button) => {
            button.addEventListener("click", () => activate(button.dataset.tabTarget));
        });
        const initialButton =
            Array.from(buttons).find((button) => button.classList.contains("is-active")) ||
            Array.from(buttons).find((button) => button.dataset.tabButton === strip.dataset.defaultTab) ||
            buttons[0];
        activate(initialButton?.dataset.tabButton || "basic");
    });
}

function setupServiceSelector(root = document) {
    const adminCheckbox = root.querySelector("#id_is_staff");
    const selector = root.querySelector("[data-service-selector]");
    const adminNote = root.querySelector("[data-admin-services-note]");
    if (!adminCheckbox || !selector) {
        return;
    }

    function syncSelectionClasses() {
        selector.querySelectorAll(".selectable-service-card").forEach((card) => {
            const checkbox = card.querySelector('input[type="checkbox"]');
            card.classList.toggle("is-selected", Boolean(checkbox?.checked));
        });
    }

    function syncState() {
        const disabled = adminCheckbox.checked;
        selector.classList.toggle("is-disabled", disabled);
        if (adminNote) {
            adminNote.hidden = !disabled;
        }
        selector.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
            checkbox.disabled = disabled;
        });
        syncSelectionClasses();
    }

    selector.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => {
        if (checkbox.dataset.boundSelectableService) {
            return;
        }
        checkbox.dataset.boundSelectableService = "1";
        checkbox.addEventListener("change", syncSelectionClasses);
    });

    if (!selector.dataset.boundSelectableServiceGrid) {
        selector.dataset.boundSelectableServiceGrid = "1";
        selector.addEventListener("click", (event) => {
            if (!event.target.closest(".selectable-service-card")) {
                return;
            }
            window.requestAnimationFrame(syncSelectionClasses);
        });
    }

    if (!adminCheckbox.dataset.boundServiceSelector) {
        adminCheckbox.dataset.boundServiceSelector = "1";
        adminCheckbox.addEventListener("change", syncState);
    }
    syncState();
}

function setupIpRuleEditors(root = document) {
    root.querySelectorAll("[data-ip-rule-editor]").forEach((editor) => {
        if (editor.dataset.ipRuleEditorReady) {
            return;
        }
        editor.dataset.ipRuleEditorReady = "1";
        const body = editor.querySelector("[data-ip-rule-body]");
        const template = editor.querySelector("[data-ip-rule-template]");
        if (!body || !template) {
            return;
        }

        editor.querySelectorAll("[data-add-ip-rule]").forEach((button) => {
            button.addEventListener("click", () => {
                const fragment = template.content.cloneNode(true);
                body.appendChild(fragment);
                const input = body.querySelector("tr:last-child input");
                if (input) {
                    input.focus();
                }
            });
        });

        editor.addEventListener("click", (event) => {
            const removeButton = event.target.closest("[data-remove-ip-rule]");
            if (!removeButton) {
                return;
            }
            removeButton.closest("tr")?.remove();
        });
    });
}

function buildPassword(length = 12) {
    const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
    const randomValues = new Uint32Array(length);
    window.crypto.getRandomValues(randomValues);
    return Array.from(randomValues, (value) => alphabet[value % alphabet.length]).join("");
}

function setupGeneratedPasswordButtons(root, secretModal) {
    root.querySelectorAll("[data-generate-password]").forEach((button) => {
        if (button.dataset.boundPasswordGenerator) {
            return;
        }
        button.dataset.boundPasswordGenerator = "1";
        button.addEventListener("click", () => {
            const password = buildPassword(12);
            const primaryField = root.querySelector("#" + button.dataset.passwordTarget);
            const confirmField = root.querySelector("#" + button.dataset.passwordConfirmTarget);
            if (primaryField) {
                primaryField.value = password;
                primaryField.dispatchEvent(new Event("input", { bubbles: true }));
            }
            if (confirmField) {
                confirmField.value = password;
                confirmField.dispatchEvent(new Event("input", { bubbles: true }));
            }
            if (button.dataset.passwordHiddenTarget) {
                const hiddenField = root.querySelector("#" + button.dataset.passwordHiddenTarget);
                if (hiddenField) {
                    hiddenField.value = "1";
                }
            }
            secretModal.open({
                eyebrow: "Временный пароль",
                title: "Новый пароль",
                subtitle: "Пароль ещё не сохранён. Он будет применён только после отправки формы.",
                value: password,
                meta: [{ label: "Минимальная длина", value: "8 символов" }],
            });
        });
    });
}

function setupDirtyTracking(root = document) {
    root.querySelectorAll("[data-modal-form]").forEach((form) => {
        if (form.dataset.boundDirtyTracking) {
            return;
        }
        form.dataset.boundDirtyTracking = "1";
        form.addEventListener("input", () => {
            form.dataset.dirty = "1";
        });
        form.addEventListener("change", () => {
            form.dataset.dirty = "1";
        });
    });
}

function setupServiceAdminForm(root = document) {
    const shell = root.querySelector("[data-service-modal-shell]");
    if (!shell || shell.dataset.boundServiceAdminForm) {
        return;
    }
    shell.dataset.boundServiceAdminForm = "1";

    const domainInput = shell.querySelector("#id_domain");
    const kindSelect = shell.querySelector("#id_kind");
    const certificateButton = shell.querySelector("[data-service-certificate-button]");
    const optionRows = Array.from(shell.querySelectorAll("[data-service-option-row]"));
    const profileButtons = Array.from(shell.querySelectorAll("[data-service-profile-apply]"));
    const optionCatalogScript = shell.querySelector("#service-option-catalog-data");
    const optionProfilesScript = shell.querySelector("#service-option-profile-data");
    const optionDocModal = window.__hubServiceOptionModal;
    let optionCatalog = {};
    let optionProfiles = [];

    try {
        optionCatalog = optionCatalogScript ? JSON.parse(optionCatalogScript.textContent) : {};
    } catch (error) {
        optionCatalog = {};
    }
    try {
        optionProfiles = optionProfilesScript ? JSON.parse(optionProfilesScript.textContent) : [];
    } catch (error) {
        optionProfiles = [];
    }

    function getOptionInput(key) {
        return shell.querySelector(`[data-service-option-input][value="${key}"]`);
    }

    function getEditableInputs() {
        return Array.from(shell.querySelectorAll("[data-service-option-input]"));
    }

    function getEditableCheckedKeys() {
        return getEditableInputs()
            .filter((input) => input.checked)
            .map((input) => input.value)
            .sort();
    }

    function syncProfileStates() {
        const currentKeys = getEditableCheckedKeys().join("|");
        profileButtons.forEach((button) => {
            const profile = optionProfiles.find((item) => item.key === button.dataset.profileKey);
            const profileKeys = (profile?.feature_keys || []).slice().sort().join("|");
            button.classList.toggle("is-active", Boolean(profileKeys) && currentKeys === profileKeys);
        });
    }

    function syncCertificateButton() {
        if (!certificateButton || !domainInput) {
            return;
        }
        certificateButton.disabled = kindSelect?.value === "external" || !domainInput.value.trim();
    }

    function syncServiceKind() {
        const isExternal = kindSelect?.value === "external";
        shell.querySelectorAll("[data-service-proxy-field], [data-service-proxy-section], [data-service-proxy-actions]").forEach((node) => {
            node.hidden = Boolean(isExternal);
        });
        shell.querySelectorAll("[data-service-external-field]").forEach((node) => {
            node.hidden = !isExternal;
        });
        shell.querySelectorAll('[data-tab-button="health"], [data-tab-button="options"], [data-tab-button="manual"]').forEach((node) => {
            node.hidden = Boolean(isExternal);
        });
        if (isExternal && !shell.querySelector('[data-tab-panel="basic"]')?.classList.contains("is-active")) {
            shell.querySelector('[data-tab-button="basic"]')?.click();
        }
        syncCertificateButton();
    }

    function syncOptionStates() {
        optionRows.forEach((row) => {
            const input = row.querySelector("[data-service-option-input]");
            const state = row.querySelector("[data-service-option-state]");
            const active = input ? input.checked : row.classList.contains("is-locked");
            row.classList.toggle("is-selected", Boolean(active));
            if (state) {
                state.classList.toggle("is-active", Boolean(active));
                state.classList.toggle("is-inactive", !active);
            }
        });
        syncProfileStates();
    }

    function enforceDependencies(sourceInput) {
        const row = sourceInput.closest("[data-service-option-row]");
        if (!row) {
            return;
        }
        const dependencies = (row.dataset.optionDependencies || "")
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);
        const conflicts = (row.dataset.optionConflicts || "")
            .split(",")
            .map((item) => item.trim())
            .filter(Boolean);

        if (sourceInput.checked) {
            dependencies.forEach((dependencyKey) => {
                const dependencyInput = getOptionInput(dependencyKey);
                if (dependencyInput) {
                    dependencyInput.checked = true;
                }
            });
            conflicts.forEach((conflictKey) => {
                const conflictInput = getOptionInput(conflictKey);
                if (conflictInput) {
                    conflictInput.checked = false;
                }
            });
        } else {
            const dependencyStillNeeded = Array.from(shell.querySelectorAll("[data-service-option-input]:checked")).some((input) => {
                const parentRow = input.closest("[data-service-option-row]");
                const parentDependencies = (parentRow?.dataset.optionDependencies || "")
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean);
                return parentDependencies.includes(sourceInput.value);
            });
            if (dependencyStillNeeded) {
                sourceInput.checked = true;
            }
        }
        syncOptionStates();
    }

    function applyFeatureSet(featureKeys) {
        const editableInputs = getEditableInputs();
        editableInputs.forEach((input) => {
            input.checked = false;
        });

        (featureKeys || []).forEach((key) => {
            const input = getOptionInput(key);
            if (input) {
                input.checked = true;
            }
        });

        editableInputs
            .filter((input) => input.checked)
            .forEach((input) => {
                enforceDependencies(input);
            });

        syncOptionStates();
    }

    if (domainInput) {
        domainInput.addEventListener("input", syncCertificateButton);
        syncCertificateButton();
    }
    if (kindSelect) {
        kindSelect.addEventListener("change", syncServiceKind);
        syncServiceKind();
    }

    optionRows.forEach((row) => {
        const input = row.querySelector("[data-service-option-input]");
        if (!input) {
            return;
        }
        input.addEventListener("change", () => enforceDependencies(input));
        row.addEventListener("click", (event) => {
            if (event.target.closest("input, button, a, label")) {
                return;
            }
            input.checked = !input.checked;
            enforceDependencies(input);
        });
    });

    profileButtons.forEach((button) => {
        if (button.dataset.boundServiceProfile) {
            return;
        }
        button.dataset.boundServiceProfile = "1";
        button.addEventListener("click", () => {
            const profile = optionProfiles.find((item) => item.key === button.dataset.profileKey);
            if (!profile) {
                return;
            }
            applyFeatureSet(profile.feature_keys || []);
            showToast(`Применён профиль «${profile.title}».`, "success");
        });
    });

    shell.querySelectorAll("[data-service-option-doc-button]").forEach((button) => {
        if (button.dataset.boundServiceOptionDoc) {
            return;
        }
        button.dataset.boundServiceOptionDoc = "1";
        button.addEventListener("click", () => {
            const option = optionCatalog[button.dataset.optionKey];
            if (!option || !optionDocModal) {
                return;
            }
            optionDocModal.open(option, button);
        });
    });

    syncOptionStates();
}

function setupActionMenus(root = document) {
    if (document.documentElement.dataset.actionMenuGlobalsReady !== "1") {
        document.documentElement.dataset.actionMenuGlobalsReady = "1";
        document.addEventListener("click", (event) => {
            if (!event.target.closest("[data-action-menu]")) {
                closeAllActionMenus();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape") {
                closeAllActionMenus();
            }
        });
    }

    root.querySelectorAll("[data-action-menu]").forEach((menu) => {
        if (menu.dataset.boundActionMenu) {
            return;
        }
        menu.dataset.boundActionMenu = "1";
        const toggle = menu.querySelector("[data-action-menu-toggle]");
        const panel = menu.querySelector("[data-action-menu-panel]");
        const serviceSlot = menu.closest(".service-slot");
        if (!toggle || !panel) {
            return;
        }

        function setOpenState(isOpen) {
            panel.hidden = !isOpen;
            menu.classList.toggle("is-open", isOpen);
            if (serviceSlot) {
                serviceSlot.classList.toggle("has-open-action-menu", isOpen);
            }
        }

        menu.__setActionMenuOpenState = setOpenState;

        toggle.addEventListener("click", (event) => {
            event.stopPropagation();
            const willOpen = panel.hidden;
            closeAllActionMenus(menu);
            setOpenState(willOpen);
        });
    });
}

function setupServiceLayoutBoard(root = document) {
    root.querySelectorAll("[data-service-layout-board]").forEach((board) => {
        if (board.dataset.boundServiceLayoutBoard) {
            return;
        }
        board.dataset.boundServiceLayoutBoard = "1";

        if (board.dataset.layoutEditable !== "1") {
            return;
        }

        const saveUrl = board.dataset.layoutSaveUrl;
        let selectedServiceId = "";
        let selectedServiceTitle = "";
        let requestInFlight = false;

        function syncBoardState() {
            board.classList.toggle("is-layout-editing", Boolean(selectedServiceId));
            board.querySelectorAll("[data-layout-slot]").forEach((slot) => {
                const slotServiceId = slot.dataset.slotServiceId || "";
                const isSelected = Boolean(selectedServiceId) && slotServiceId === selectedServiceId;
                const isTarget = Boolean(selectedServiceId) && slotServiceId !== selectedServiceId;
                slot.classList.toggle("is-selected", isSelected);
                slot.classList.toggle("is-move-target", isTarget);

                const pinButton = slot.querySelector("[data-layout-pin]");
                if (!pinButton) {
                    return;
                }
                const serviceTitle = pinButton.dataset.serviceTitle || "сервис";
                pinButton.setAttribute("aria-pressed", isSelected ? "true" : "false");
                pinButton.setAttribute(
                    "aria-label",
                    isSelected
                        ? `Отменить перемещение сервиса ${serviceTitle}`
                        : `Выбрать сервис ${serviceTitle} для перемещения`
                );
                pinButton.setAttribute(
                    "title",
                    isSelected
                        ? `Отменить перемещение сервиса ${serviceTitle}`
                        : `Выбрать сервис ${serviceTitle} для перемещения`
                );
            });
        }

        function clearSelection() {
            selectedServiceId = "";
            selectedServiceTitle = "";
            syncBoardState();
        }

        function replaceBoard(boardHtml) {
            if (!replaceServiceLayoutBoardMarkup(boardHtml)) {
                clearSelection();
            }
        }

        async function moveSelectedService(slotIndex) {
            if (!selectedServiceId || requestInFlight || !saveUrl) {
                return;
            }

            const payload = new FormData();
            payload.append("service_id", selectedServiceId);
            payload.append("slot_index", String(slotIndex));

            requestInFlight = true;
            board.classList.add("is-busy");
            try {
                const response = await fetchJson(saveUrl, {
                    method: "POST",
                    body: payload,
                });
                replaceBoard(response.board_html || "");
                showToast(response.message || `Положение сервиса ${selectedServiceTitle} сохранено.`, "success");
            } catch (error) {
                showToast(error?.message || "Не удалось сохранить новую раскладку сервисов.", "error");
            } finally {
                requestInFlight = false;
                if (board.isConnected) {
                    board.classList.remove("is-busy");
                }
            }
        }

        board.addEventListener("click", (event) => {
            const pinButton = event.target.closest("[data-layout-pin]");
            if (pinButton) {
                event.preventDefault();
                event.stopPropagation();
                const nextServiceId = pinButton.dataset.serviceId || "";
                if (!nextServiceId) {
                    return;
                }
                if (selectedServiceId === nextServiceId) {
                    clearSelection();
                    return;
                }
                selectedServiceId = nextServiceId;
                selectedServiceTitle = pinButton.dataset.serviceTitle || "";
                syncBoardState();
                return;
            }

            if (!selectedServiceId) {
                return;
            }

            const slot = event.target.closest("[data-layout-slot]");
            if (!slot || !board.contains(slot)) {
                return;
            }
            const slotServiceId = slot.dataset.slotServiceId || "";
            if (slotServiceId === selectedServiceId) {
                event.preventDefault();
                return;
            }

            event.preventDefault();
            event.stopPropagation();
            moveSelectedService(Number.parseInt(slot.dataset.slotIndex || "0", 10) || 0);
        });

        syncBoardState();
    });
}

function setupServiceLayoutReset(root = document) {
    root.querySelectorAll("[data-layout-reset]").forEach((button) => {
        if (button.dataset.boundLayoutReset === "1") {
            return;
        }
        button.dataset.boundLayoutReset = "1";

        const resetUrl = button.dataset.layoutResetUrl;
        let requestInFlight = false;

        button.addEventListener("click", async () => {
            if (requestInFlight || !resetUrl) {
                return;
            }

            requestInFlight = true;
            button.disabled = true;
            document.querySelector("[data-service-layout-board]")?.classList.add("is-busy");

            try {
                const response = await fetchJson(resetUrl, { method: "POST" });
                replaceServiceLayoutBoardMarkup(response.board_html || "");
                showToast(response.message || "Раскладка сервисов сброшена.", "success");
            } catch (error) {
                showToast(error?.message || "Не удалось сбросить раскладку сервисов.", "error");
            } finally {
                requestInFlight = false;
                button.disabled = false;
                document.querySelector("[data-service-layout-board]")?.classList.remove("is-busy");
            }
        });
    });
}

function setupServiceCards() {
    document.querySelectorAll("[data-service-card]").forEach((card) => {
        if (card.dataset.boundServiceCard) {
            return;
        }
        card.dataset.boundServiceCard = "1";
        function blockIfNeeded(event) {
            if (card.dataset.serviceStatus === "down") {
                event.preventDefault();
                event.stopPropagation();
            }
        }
        card.addEventListener("click", blockIfNeeded);
        card.addEventListener("keydown", (event) => {
            if ((event.key === "Enter" || event.key === " ") && card.dataset.serviceStatus === "down") {
                event.preventDefault();
            }
        });
    });
}

function syncServiceCards(payload) {
    const counts = { up: 0, down: 0, unknown: 0 };
    payload.services.forEach((item) => {
        const card = document.querySelector(`[data-service-card][data-service-id="${item.id}"]`);
        if (!card) {
            return;
        }
        counts[item.status] = (counts[item.status] || 0) + 1;
        card.dataset.serviceStatus = item.status;
        card.classList.remove("service-card-up", "service-card-down", "service-card-unknown");
        card.classList.add(
            item.status === "up" ? "service-card-up" : item.status === "down" ? "service-card-down" : "service-card-unknown"
        );
        const label = STATUS_LABELS[item.status] || STATUS_LABELS.unknown;
        card.title = label;
        if (item.status === "down") {
            card.setAttribute("aria-disabled", "true");
            card.setAttribute("aria-label", label);
        } else {
            card.removeAttribute("aria-disabled");
            const title = card.querySelector("h3")?.textContent || "";
            card.setAttribute(
                "aria-label",
                item.status === "unknown" ? `Открыть сервис ${title}. Статус ещё проверяется` : `Открыть сервис ${title}`
            );
        }
    });

    document.querySelectorAll("[data-service-metric]").forEach((node) => {
        const key = node.dataset.serviceMetric;
        if (key === "total") {
            return;
        }
        node.textContent = String(counts[key] || 0);
    });
}

async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        headers: {
            Accept: "application/json",
            "X-CSRFToken": getCookie("csrftoken"),
            "X-Requested-With": "XMLHttpRequest",
            ...options.headers,
        },
        ...options,
    });

    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json") ? await response.json() : {};
    if (!response.ok) {
        throw payload;
    }
    return payload;
}

function buildSecretModalConfig(payload, fallbackTitle) {
    return {
        eyebrow: "Секрет",
        title: fallbackTitle,
        value: payload.token || payload.password || "",
        meta: [
            { label: "Статус", value: payload.status_label || "—" },
            { label: "Создан", value: payload.generated_at || "—" },
            { label: "Последнее использование", value: payload.last_used_at || "—" },
            { label: "Создал", value: payload.created_by || "—" },
        ],
    };
}

function syncTokenUi(payload, root = document) {
    root.querySelectorAll("[data-token-status-pill]").forEach((node) => {
        node.textContent = payload.status_label || (payload.can_reveal ? "Токен активен" : "Токен не создан");
    });
    root.querySelectorAll("[data-account-token-status]").forEach((node) => {
        node.textContent = payload.status_label || "—";
    });
    root.querySelectorAll("[data-account-token-created]").forEach((node) => {
        node.textContent = payload.generated_at || "—";
    });
    root.querySelectorAll("[data-account-token-last-used]").forEach((node) => {
        node.textContent = payload.last_used_at || "—";
    });
    root.querySelectorAll("[data-account-token-created-by]").forEach((node) => {
        node.textContent = payload.created_by || "—";
    });
    root.querySelectorAll("[data-token-reveal-url]").forEach((button) => {
        button.disabled = !payload.can_reveal;
    });
    root.querySelectorAll("[data-token-revoke-url]").forEach((button) => {
        button.disabled = !payload.has_token;
    });
    root.querySelectorAll("[data-token-generate-url]").forEach((button) => {
        button.textContent = payload.has_token ? "Ротировать токен" : "Создать токен";
    });
}

function currentPageUrl() {
    return window.location.pathname + window.location.search;
}

async function refreshFragments() {
    const fragments = Array.from(document.querySelectorAll("[data-refresh-fragment]"));
    for (const node of fragments) {
        const fragmentName = node.dataset.refreshFragment;
        const url = new URL(window.location.href);
        url.searchParams.set("fragment", fragmentName);
        const response = await fetch(url.toString(), { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
        const html = await response.text();
        const wrapper = document.createElement("div");
        wrapper.innerHTML = html.trim();
        const replacement = wrapper.firstElementChild;
        if (replacement) {
            node.replaceWith(replacement);
        }
    }
    initializeInteractiveUi(document);
}

function setupAppModal(secretModal) {
    const shell = document.querySelector("[data-app-modal]");
    if (!shell) {
        return { openFromUrl() {}, close() {} };
    }

    const contentNode = shell.querySelector("[data-app-modal-content]");
    const cardNode = shell.querySelector(".modal-card");
    let restoreFocus = null;
    let releaseFocusTrap = null;
    const modalApi = {
        openFromUrl,
        close,
    };

    function getActiveForm() {
        return shell.querySelector("[data-modal-form]");
    }

    function syncModalVariant() {
        if (!cardNode) {
            return;
        }
        const isServiceModal = Boolean(contentNode.querySelector("[data-service-modal-shell]"));
        cardNode.classList.toggle("is-service-modal", isServiceModal);
    }

    function close(force = false) {
        const form = getActiveForm();
        if (!force && form && form.dataset.dirty === "1" && !form.dataset.submitting) {
            const shouldClose = window.confirm("Есть несохранённые изменения. Закрыть окно?");
            if (!shouldClose) {
                return;
            }
        }
        shell.hidden = true;
        document.body.classList.remove("is-scroll-locked");
        contentNode.innerHTML = "";
        syncModalVariant();
        if (releaseFocusTrap) {
            releaseFocusTrap();
            releaseFocusTrap = null;
        }
        if (restoreFocus) {
            restoreFocus.focus();
            restoreFocus = null;
        }
    }

    async function openFromUrl(url, opener = document.activeElement) {
        restoreFocus = opener;
        const response = await fetch(url, { credentials: "same-origin", headers: { "X-Requested-With": "XMLHttpRequest" } });
        const html = await response.text();
        contentNode.innerHTML = html;
        syncModalVariant();
        shell.hidden = false;
        document.body.classList.add("is-scroll-locked");
        if (releaseFocusTrap) {
            releaseFocusTrap();
        }
        releaseFocusTrap = createFocusTrap(shell.querySelector(".modal-card"));
        initializeInteractiveUi(contentNode, { secretModal, appModal: modalApi });
        const firstInput = contentNode.querySelector("input, button, select, textarea");
        if (firstInput) {
            firstInput.focus();
        }
    }

    shell.querySelectorAll("[data-app-modal-dismiss]").forEach((button) => {
        button.addEventListener("click", () => close());
    });
    shell.addEventListener("click", (event) => {
        if (event.target.matches("[data-app-modal-dismiss]")) {
            close();
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !shell.hidden) {
            close();
        }
    });

    shell.addEventListener("submit", async (event) => {
        const form = event.target.closest("[data-modal-form]");
        if (!form) {
            return;
        }
        event.preventDefault();
        form.dataset.submitting = "1";
        try {
            const submitter = event.submitter instanceof HTMLElement ? event.submitter : null;
            const formData = new FormData(form);
            if (submitter && submitter.getAttribute("name")) {
                formData.set(submitter.getAttribute("name"), submitter.getAttribute("value") || "");
            }
            const response = await fetch(submitter?.getAttribute("formaction") || form.action, {
                method: "POST",
                credentials: "same-origin",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                body: formData,
            });
            const contentType = response.headers.get("content-type") || "";
            if (contentType.includes("application/json")) {
                const payload = await response.json();
                if (!response.ok) {
                    throw payload;
                }
                if (payload.keep_open) {
                    if (payload.html) {
                        contentNode.innerHTML = payload.html;
                        syncModalVariant();
                        initializeInteractiveUi(contentNode, { secretModal, appModal: modalApi });
                    }
                    if (payload.message) {
                        showToast(payload.message, "success");
                    }
                    if (payload.refresh) {
                        await refreshFragments();
                    }
                    return;
                }
                close(true);
                if (payload.message) {
                    showToast(payload.message, "success");
                }
                if (payload.refresh) {
                    await refreshFragments();
                }
                return;
            }
            contentNode.innerHTML = await response.text();
            syncModalVariant();
            initializeInteractiveUi(contentNode, { secretModal, appModal: modalApi });
        } catch (payload) {
            if (payload && payload.html) {
                contentNode.innerHTML = payload.html;
                syncModalVariant();
                initializeInteractiveUi(contentNode, { secretModal, appModal: modalApi });
            } else {
                showToast(payload?.message || "Не удалось сохранить форму.", "error");
            }
        } finally {
            form.dataset.submitting = "";
        }
    });

    return modalApi;
}

async function handleJsonAction(button, secretModal) {
    const url =
        button.dataset.jsonPost ||
        button.dataset.tokenGenerateUrl ||
        button.dataset.tokenRevealUrl ||
        button.dataset.tokenRotateUrl ||
        button.dataset.tokenRevokeUrl ||
        button.dataset.requirePasswordChangeUrl;
    if (!url) {
        return;
    }
    if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) {
        return;
    }

    button.disabled = true;
    try {
        const payload = await fetchJson(url, { method: "POST" });
        if (button.dataset.tokenGenerateUrl || button.dataset.tokenRevealUrl || button.dataset.tokenRotateUrl) {
            syncTokenUi(payload);
            secretModal.open(buildSecretModalConfig(payload, "Токен входа"));
        } else if (button.dataset.tokenRevokeUrl) {
            syncTokenUi(payload);
            showToast("Токен отозван.", "success");
        } else if (payload.logged_out) {
            window.location.href = "/";
            return;
        } else {
            showToast(payload.message || "Действие выполнено.", "success");
        }
        if (button.dataset.jsonPost || button.dataset.requirePasswordChangeUrl || button.dataset.tokenRevokeUrl) {
            await refreshFragments().catch(() => {});
        }
    } catch (payload) {
        showToast(payload?.message || "Не удалось выполнить действие.", "error");
    } finally {
        button.disabled = false;
    }
}

function setupJsonButtons(root, secretModal) {
    root.querySelectorAll(
        "[data-json-post], [data-token-generate-url], [data-token-reveal-url], [data-token-rotate-url], [data-token-revoke-url], [data-require-password-change-url]"
    ).forEach((button) => {
        if (button.dataset.boundJsonAction) {
            return;
        }
        button.dataset.boundJsonAction = "1";
        button.addEventListener("click", () => {
            if (button.disabled) {
                return;
            }
            handleJsonAction(button, secretModal);
        });
    });
}

function initializeInteractiveUi(root = document, dependencies = {}) {
    const secretModal = dependencies.secretModal || window.__hubSecretModal;
    const appModal = dependencies.appModal || window.__hubAppModal;

    setupThemeToggle();
    setupAuditClearDialog();
    setupAuthSwitch(root);
    setupPasswordVisibility(root);
    setupAuthForm(root);
    setupTabs(root);
    setupDirtyTracking(root);
    setupServiceSelector(root);
    setupIpRuleEditors(root);
    setupServiceAdminForm(root);
    setupGeneratedPasswordButtons(root, secretModal);
    setupEnhancedSelects(root);
    setupActionMenus(root);
    setupJsonButtons(root, secretModal);
    setupServiceLayoutBoard(root);
    setupServiceLayoutReset(root);
    setupServiceCards();
    togglePeriodCustomFields();

    root.querySelectorAll("[data-open-modal-url]").forEach((button) => {
        if (button.dataset.boundOpenModal) {
            return;
        }
        button.dataset.boundOpenModal = "1";
        button.addEventListener("click", (event) => {
            event.preventDefault();
            appModal.openFromUrl(button.dataset.openModalUrl, button);
        });
    });

    root.querySelectorAll("[data-row-link]").forEach((row) => {
        if (row.dataset.boundInteractiveRow) {
            return;
        }
        row.dataset.boundInteractiveRow = "1";
        function activateRow() {
            if (row.dataset.rowModal === "1") {
                appModal.openFromUrl(row.dataset.rowLink + "?modal=1", row);
            } else {
                window.location.href = row.dataset.rowLink;
            }
        }
        row.addEventListener("click", (event) => {
            if (event.target.closest("a, button, input, label, select, textarea")) {
                return;
            }
            activateRow();
        });
        row.addEventListener("keydown", (event) => {
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                activateRow();
            }
        });
    });
}

function setupServiceStatusPolling() {
    const grid = document.querySelector("[data-service-grid]");
    if (!grid) {
        return;
    }

    if (document.documentElement.dataset.serviceStatusPollingReady === "1") {
        return;
    }
    document.documentElement.dataset.serviceStatusPollingReady = "1";

    async function refresh({ force = false } = {}) {
        if (serviceStatusPollInFlight || (!force && document.hidden)) {
            return;
        }
        serviceStatusPollInFlight = true;
        try {
            const payload = await fetchJson("/api/services/statuses/", { method: "GET", headers: { Accept: "application/json" } });
            syncServiceCards(payload);
        } catch (error) {
            // Silent by design: background polling must not spam users.
        } finally {
            serviceStatusPollInFlight = false;
        }
    }

    function schedule(delay = SERVICE_STATUS_POLL_INTERVAL_MS) {
        if (serviceStatusPollTimer) {
            window.clearTimeout(serviceStatusPollTimer);
        }
        serviceStatusPollTimer = window.setTimeout(async () => {
            await refresh();
            schedule();
        }, delay);
    }

    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) {
            refresh({ force: true });
            schedule();
            return;
        }
        if (serviceStatusPollTimer) {
            window.clearTimeout(serviceStatusPollTimer);
            serviceStatusPollTimer = null;
        }
    });

    refresh({ force: true });
    schedule();
}

document.addEventListener("DOMContentLoaded", () => {
    const secretModal = setupSecretModal();
    window.__hubSecretModal = secretModal;
    const serviceOptionModal = setupServiceOptionModal();
    window.__hubServiceOptionModal = serviceOptionModal;
    const appModal = setupAppModal(secretModal);
    window.__hubAppModal = appModal;

    setupReleaseWatcher();
    setupMobileNav();
    initializeInteractiveUi(document, { secretModal, appModal });
    setupServiceStatusPolling();
});
