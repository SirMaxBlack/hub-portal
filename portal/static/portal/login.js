const AUTH_METHOD_ACCOUNT = "account";
const AUTH_METHOD_TOKEN = "token";
const THEME_KEY = "hubPortalTheme";
const RELEASE_RELOAD_KEY = "hubPortalReloadReleaseId";
const RELEASE_CHECK_INTERVAL_MS = 15000;
const AUTH_PANEL_HEIGHT_BUFFER = 20;

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

async function fetchJson(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        headers: {
            Accept: "application/json",
            "X-CSRFToken": getCookie("csrftoken"),
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
    document.dispatchEvent(new CustomEvent("hub:theme-changed", { detail: { theme } }));
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
            // Silent by design.
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

    function measurePanelHeight(panel) {
        if (!stage || !panel) {
            return 0;
        }

        const previousStageHeight = stage.style.height;
        const previousPanelStyles = {
            position: panel.style.position,
            inset: panel.style.inset,
            visibility: panel.style.visibility,
            opacity: panel.style.opacity,
            pointerEvents: panel.style.pointerEvents,
            transform: panel.style.transform,
            zIndex: panel.style.zIndex,
            width: panel.style.width,
        };
        const previousHidden = panel.hidden;

        panel.hidden = false;
        stage.style.height = "auto";
        panel.style.position = "relative";
        panel.style.inset = "auto";
        panel.style.visibility = "hidden";
        panel.style.opacity = "1";
        panel.style.pointerEvents = "none";
        panel.style.transform = "none";
        panel.style.zIndex = "0";
        panel.style.width = "100%";

        const height = Math.ceil(panel.scrollHeight) + AUTH_PANEL_HEIGHT_BUFFER;

        panel.hidden = previousHidden;
        panel.style.position = previousPanelStyles.position;
        panel.style.inset = previousPanelStyles.inset;
        panel.style.visibility = previousPanelStyles.visibility;
        panel.style.opacity = previousPanelStyles.opacity;
        panel.style.pointerEvents = previousPanelStyles.pointerEvents;
        panel.style.transform = previousPanelStyles.transform;
        panel.style.zIndex = previousPanelStyles.zIndex;
        panel.style.width = previousPanelStyles.width;
        stage.style.height = previousStageHeight;

        return height;
    }

    function syncStageHeight(activeMode = currentMode, { immediate = false } = {}) {
        if (!stage) {
            return;
        }
        const activePanel = getPanelByMode(activeMode);
        if (!activePanel) {
            return;
        }
        const targetHeight = measurePanelHeight(activePanel);
        if (immediate) {
            stage.classList.add("is-static");
        }
        stage.style.height = `${targetHeight}px`;
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
        currentMode = mode;
        hiddenInput.value = mode;
        switchRoot.style.setProperty("--auth-switch-index", mode === AUTH_METHOD_TOKEN ? "1" : "0");
        buttons.forEach((button) => {
            button.classList.toggle("is-active", button.dataset.authModeButton === mode);
            button.setAttribute("aria-pressed", button.dataset.authModeButton === mode ? "true" : "false");
        });
        const currentHeight = stage ? Math.ceil(stage.getBoundingClientRect().height) : 0;
        placePanels(mode);
        const nextPanel = getPanelByMode(mode);
        const targetHeight = measurePanelHeight(nextPanel);
        if (!stage || immediate) {
            syncStageHeight(mode, { immediate: true });
        } else {
            stage.style.height = `${currentHeight || targetHeight}px`;
            stage.offsetHeight;
            window.requestAnimationFrame(() => {
                stage.style.height = `${targetHeight}px`;
            });
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

function getCanvasFingerprint() {
    try {
        const canvas = document.createElement("canvas");
        canvas.width = 220;
        canvas.height = 60;
        const context = canvas.getContext("2d");
        if (!context) {
            return "";
        }
        context.textBaseline = "top";
        context.font = '16px "Segoe UI", Arial, sans-serif';
        context.fillStyle = "#1b6fb5";
        context.fillRect(4, 4, 120, 28);
        context.fillStyle = "#ffffff";
        context.fillText("HUB", 8, 10);
        context.strokeStyle = "rgba(4, 16, 29, 0.55)";
        context.beginPath();
        context.arc(180, 24, 16, 0, Math.PI * 2);
        context.stroke();
        return canvas.toDataURL();
    } catch (error) {
        return "";
    }
}

function getWebGlFingerprint() {
    try {
        const canvas = document.createElement("canvas");
        const gl =
            canvas.getContext("webgl") ||
            canvas.getContext("experimental-webgl");
        if (!gl) {
            return "";
        }
        const debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
        const vendor = debugInfo ? gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) : "";
        const renderer = debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : "";
        return [vendor, renderer, gl.getParameter(gl.VERSION) || ""].join("|");
    } catch (error) {
        return "";
    }
}

function buildBrowserFingerprint() {
    const nav = window.navigator || {};
    const screenInfo = window.screen || {};
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
    const languages = Array.isArray(nav.languages) ? nav.languages.join(",") : "";
    const parts = [
        nav.userAgent || "",
        nav.platform || "",
        nav.vendor || "",
        nav.language || "",
        languages,
        String(nav.hardwareConcurrency || ""),
        String(nav.deviceMemory || ""),
        String(nav.maxTouchPoints || ""),
        String(nav.cookieEnabled || ""),
        String(nav.doNotTrack || ""),
        String(window.devicePixelRatio || ""),
        String(screenInfo.width || ""),
        String(screenInfo.height || ""),
        String(screenInfo.availWidth || ""),
        String(screenInfo.availHeight || ""),
        String(screenInfo.colorDepth || ""),
        timezone,
        getCanvasFingerprint(),
        getWebGlFingerprint(),
    ];
    return parts.join("|");
}

function setupBrowserFingerprint(root = document) {
    const input = root.querySelector("[data-browser-fingerprint-input]");
    if (!input) {
        return;
    }
    input.value = buildBrowserFingerprint();
}

let activeLoginFeedbackTimer = 0;

function clearTimeoutSafe(timerId) {
    if (timerId) {
        window.clearTimeout(timerId);
    }
}

function clearAuthErrors(form) {
    if (!form) {
        return;
    }
    const formError = form.querySelector("[data-form-error]");
    if (formError) {
        formError.textContent = "";
        formError.hidden = true;
    }
    form.querySelectorAll("[data-field-error-for]").forEach((node) => {
        node.textContent = "";
        node.hidden = true;
    });
}

function applyAuthErrors(form, payload = {}) {
    if (!form) {
        return;
    }
    const nonFieldErrors = Array.isArray(payload.non_field_errors) ? payload.non_field_errors.filter(Boolean) : [];
    const formError = form.querySelector("[data-form-error]");
    if (formError) {
        formError.textContent = nonFieldErrors.join(" ");
        formError.hidden = nonFieldErrors.length === 0;
    }

    const fieldErrors = payload.field_errors && typeof payload.field_errors === "object" ? payload.field_errors : {};
    Object.entries(fieldErrors).forEach(([fieldName, messages]) => {
        const node = form.querySelector(`[data-field-error-for="${fieldName}"]`);
        if (!node) {
            return;
        }
        const normalizedMessages = Array.isArray(messages) ? messages.filter(Boolean) : [];
        node.textContent = normalizedMessages.join(" ");
        node.hidden = normalizedMessages.length === 0;
    });
}

function focusFirstAuthError(form) {
    if (!form) {
        return;
    }
    const firstFieldError = form.querySelector('[data-field-error-for]:not([hidden])');
    if (firstFieldError?.dataset.fieldErrorFor) {
        const input = form.querySelector(`#id_${firstFieldError.dataset.fieldErrorFor}`);
        if (input) {
            input.focus();
            return;
        }
    }
    const activeInput = form.querySelector('[data-auth-panel].is-active input:not([type="hidden"])');
    activeInput?.focus();
}

function ensureLoginFeedbackModal(root = document, title = "Доступ ограничен") {
    const stage = root.querySelector(".auth-stage");
    if (!stage) {
        return null;
    }
    let modal = stage.querySelector("[data-login-feedback-modal]");
    if (!modal) {
        modal = document.createElement("div");
        modal.className = "modal-shell login-feedback-modal";
        modal.dataset.loginFeedbackModal = "1";
        modal.innerHTML = `
            <div class="modal-backdrop"></div>
            <section class="glass-panel modal-card login-feedback-card" role="alertdialog" aria-modal="true" aria-labelledby="login-feedback-title">
                <div class="auth-card-head">
                    <div class="auth-card-titlebar">
                        <h2 id="login-feedback-title" data-login-feedback-title></h2>
                    </div>
                </div>
            </section>
        `;
        stage.appendChild(modal);
    }
    const titleNode = modal.querySelector("[data-login-feedback-title]") || modal.querySelector("#login-feedback-title");
    if (titleNode) {
        titleNode.textContent = title;
    }
    return modal;
}

function hideLoginFeedbackModal(root = document) {
    clearTimeoutSafe(activeLoginFeedbackTimer);
    activeLoginFeedbackTimer = 0;
    root.querySelector("[data-login-feedback-modal]")?.remove();
}

function showLoginFeedbackModal(root = document, { title = "Доступ ограничен", durationMs = 5000 } = {}) {
    const modal = ensureLoginFeedbackModal(root, title);
    if (!modal) {
        return;
    }
    modal.dataset.loginFeedbackDuration = String(Math.max(0, durationMs || 0));
    clearTimeoutSafe(activeLoginFeedbackTimer);
    activeLoginFeedbackTimer = 0;
    if (durationMs > 0) {
        activeLoginFeedbackTimer = window.setTimeout(() => {
            modal.remove();
            activeLoginFeedbackTimer = 0;
        }, durationMs);
    }
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

    function setSubmittingState(isSubmitting) {
        if (!submitButton) {
            return;
        }
        if (isSubmitting) {
            submitButton.disabled = true;
            if (spinner) {
                spinner.hidden = false;
            }
            if (submitLabel) {
                submitLabel.textContent = "Выполняется вход";
            }
            return;
        }
        if (spinner) {
            spinner.hidden = true;
        }
        if (submitLabel) {
            submitLabel.textContent = "Войти";
        }
        syncSubmitState();
    }

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

    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (!submitButton || submitButton.disabled) {
            return;
        }
        setSubmittingState(true);
        clearAuthErrors(form);

        try {
            const response = await fetch(form.action || window.location.href, {
                method: "POST",
                credentials: "same-origin",
                headers: {
                    Accept: "application/json",
                    "X-CSRFToken": getCookie("csrftoken"),
                    "X-Requested-With": "XMLHttpRequest",
                },
                body: new FormData(form),
            });
            const contentType = response.headers.get("content-type") || "";
            const payload = contentType.includes("application/json") ? await response.json() : {};

            if (response.ok && payload.ok) {
                hideLoginFeedbackModal(root);
                window.location.href = payload.redirect_url || "/services/";
                return;
            }

            applyAuthErrors(form, payload);
            if (payload.show_ip_access_denied) {
                showLoginFeedbackModal(root, {
                    title: "Доступ ограничен",
                    durationMs: Number(payload.revert_delay_ms || 5000),
                });
            } else {
                hideLoginFeedbackModal(root);
            }
            if (payload.particle_variant) {
                document.dispatchEvent(
                    new CustomEvent("hub:login-particles-variant-changed", {
                        detail: {
                            variant: payload.particle_variant,
                            revertDelay: Number(payload.revert_delay_ms || 0),
                        },
                    })
                );
            }
            focusFirstAuthError(form);
        } catch (error) {
            applyAuthErrors(form, {
                non_field_errors: ["Не удалось выполнить вход. Повторите попытку."],
            });
        } finally {
            setSubmittingState(false);
        }
    });

    syncSubmitState();
}

function setupLoginFeedbackModal(root = document) {
    const modal = root.querySelector("[data-login-feedback-modal]");
    if (!modal) {
        return;
    }
    const title = modal.querySelector("[data-login-feedback-title]")?.textContent?.trim() || "Доступ ограничен";
    const delayMs = Math.max(0, Number(modal.dataset.loginFeedbackDuration || 5000) || 5000);
    showLoginFeedbackModal(root, { title, durationMs: delayMs });
}

document.addEventListener("DOMContentLoaded", () => {
    setupThemeToggle();
    setupReleaseWatcher();
    setupAuthSwitch(document);
    setupPasswordVisibility(document);
    setupBrowserFingerprint(document);
    setupAuthForm(document);
    setupLoginFeedbackModal(document);
});
