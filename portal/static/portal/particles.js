const LOGIN_PARTICLE_FPS = 30;
const LOGIN_PARTICLE_DESKTOP_COUNT = 150;
const LOGIN_PARTICLE_MOBILE_COUNT = 50;
const LOGIN_PARTICLE_CALM_SPEED_PX = 38;
const LOGIN_PARTICLE_ALERT_SPEED_PX = 189;
const LOGIN_PARTICLE_FADE_DURATION_MS = 1000;
const LOGIN_PARTICLE_ALERT_RAMP_MS = 220;

function setupLoginParticles(root = document) {
    const body = document.body;
    const stage = root.querySelector(".auth-stage");
    const canvas = root.querySelector("[data-login-particles-canvas]");
    if (!body?.classList.contains("login-shell") || !stage || !canvas || canvas.dataset.boundLoginParticles === "1") {
        return;
    }
    canvas.dataset.boundLoginParticles = "1";

    const context = canvas.getContext("2d", { alpha: true, desynchronized: true }) || canvas.getContext("2d");
    if (!context) {
        return;
    }

    const reduceMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const mobileQuery = window.matchMedia("(max-width: 768px)");
    const coarsePointerQuery = window.matchMedia("(hover: none) and (pointer: coarse)");
    const frameInterval = 1000 / LOGIN_PARTICLE_FPS;
    const supportedVariants = new Set(["calm", "alert"]);
    const persistKey = "hubLoginParticlesState:v8";
    const persistMaxAgeMs = 15000;
    const pointer = {
        active: false,
        x: 0,
        y: 0,
    };

    let width = 1;
    let height = 1;
    let dpr = 1;
    let particles = [];
    let rafId = 0;
    let lastFrameTime = 0;
    let isDocumentVisible = !document.hidden;
    let resizeTimer = 0;
    let revertTimer = 0;
    let configuredDesktopCount = LOGIN_PARTICLE_DESKTOP_COUNT;
    let configuredMobileCount = LOGIN_PARTICLE_MOBILE_COUNT;
    let calmSpeedPx = LOGIN_PARTICLE_CALM_SPEED_PX;
    let alertSpeedPx = LOGIN_PARTICLE_ALERT_SPEED_PX;
    let fadeDurationMs = LOGIN_PARTICLE_FADE_DURATION_MS;
    let cachedStoredSnapshot;
    let baseVariant = readStageVariant("loginParticleBaseVariant", "calm");
    let currentVariant = readStageVariant("loginParticleVariant", baseVariant);
    let activePalette = clonePalette(readPalette(currentVariant));
    let paletteFrom = clonePalette(activePalette);
    let paletteTo = clonePalette(activePalette);
    let activeSpeedMultiplier = getVariantSpeedMultiplier(currentVariant);
    let speedFrom = activeSpeedMultiplier;
    let speedTo = activeSpeedMultiplier;
    let transitionStartedAt = 0;
    let transitionDuration = 0;
    let hasBootstrapped = false;

    function clamp(value, min, max) {
        return Math.min(max, Math.max(min, value));
    }

    function readPositiveInt(rawValue, fallback, min = 1, max = 1000) {
        const parsed = Number(rawValue);
        if (!Number.isFinite(parsed)) {
            return fallback;
        }
        const normalized = Math.round(parsed);
        if (normalized < min || normalized > max) {
            return fallback;
        }
        return normalized;
    }

    function readStageVariant(attributeName, fallback = "calm") {
        const candidate = String(stage.dataset[attributeName] || fallback || "calm").trim().toLowerCase();
        return supportedVariants.has(candidate) ? candidate : "calm";
    }

    function readStageRevertDelay() {
        const rawValue = Number(stage.dataset.loginParticleRevertDelay || 0);
        return Number.isFinite(rawValue) && rawValue > 0 ? rawValue : 0;
    }

    function getTransitionDuration(nextVariant) {
        const normalizedFadeDuration = Math.max(120, fadeDurationMs);
        if (nextVariant === "alert") {
            return Math.min(normalizedFadeDuration, LOGIN_PARTICLE_ALERT_RAMP_MS);
        }
        return normalizedFadeDuration;
    }

    function parseColor(value, fallback) {
        const match = String(value || "").trim().match(/rgba?\(([^)]+)\)/i);
        if (!match) {
            return fallback.slice();
        }
        const parts = match[1]
            .split(",")
            .map((part) => Number(part.trim()))
            .filter((part) => Number.isFinite(part));
        if (parts.length < 3) {
            return fallback.slice();
        }
        return [
            clamp(parts[0], 0, 255),
            clamp(parts[1], 0, 255),
            clamp(parts[2], 0, 255),
            clamp(parts[3] ?? fallback[3], 0, 1),
        ];
    }

    function colorToCss(color) {
        return `rgba(${Math.round(color[0])}, ${Math.round(color[1])}, ${Math.round(color[2])}, ${color[3].toFixed(3)})`;
    }

    function clonePalette(palette) {
        return {
            dot: palette.dot.slice(),
            dotSoft: palette.dotSoft.slice(),
            line: palette.line.slice(),
            pointerLine: palette.pointerLine.slice(),
        };
    }

    function mixColor(from, to, progress) {
        return [
            from[0] + (to[0] - from[0]) * progress,
            from[1] + (to[1] - from[1]) * progress,
            from[2] + (to[2] - from[2]) * progress,
            from[3] + (to[3] - from[3]) * progress,
        ];
    }

    function mixPalette(from, to, progress) {
        return {
            dot: mixColor(from.dot, to.dot, progress),
            dotSoft: mixColor(from.dotSoft, to.dotSoft, progress),
            line: mixColor(from.line, to.line, progress),
            pointerLine: mixColor(from.pointerLine, to.pointerLine, progress),
        };
    }

    function getVariantSpeedMultiplier(variant = currentVariant) {
        return variant === "alert" ? alertSpeedPx / Math.max(calmSpeedPx, 1) : 1;
    }

    function readPalette(variant = currentVariant) {
        const styles = getComputedStyle(document.documentElement);
        const prefix = variant === "alert" ? "--login-particle-alert" : "--login-particle";
        const defaults =
            variant === "alert"
                ? {
                    dot: [255, 212, 220, 0.86],
                    dotSoft: [255, 88, 121, 0.84],
                    line: [255, 160, 176, 0.22],
                    pointerLine: [255, 240, 244, 0.34],
                }
                : {
                    dot: [242, 247, 255, 0.84],
                    dotSoft: [108, 212, 255, 0.76],
                    line: [132, 216, 255, 0.17],
                    pointerLine: [190, 236, 255, 0.28],
                };
        return {
            dot: parseColor(styles.getPropertyValue(`${prefix}-dot`).trim(), defaults.dot),
            dotSoft: parseColor(styles.getPropertyValue(`${prefix}-dot-soft`).trim(), defaults.dotSoft),
            line: parseColor(styles.getPropertyValue(`${prefix}-line`).trim(), defaults.line),
            pointerLine: parseColor(styles.getPropertyValue(`${prefix}-pointer-line`).trim(), defaults.pointerLine),
        };
    }

    function getBaseParticleSpeedPxPerSecond() {
        return Math.max(calmSpeedPx, 1);
    }

    function readConfiguredValues() {
        configuredDesktopCount = readPositiveInt(stage.dataset.loginParticleDesktopCount, LOGIN_PARTICLE_DESKTOP_COUNT, 1, 400);
        configuredMobileCount = readPositiveInt(stage.dataset.loginParticleMobileCount, LOGIN_PARTICLE_MOBILE_COUNT, 1, 400);
        calmSpeedPx = readPositiveInt(stage.dataset.loginParticleCalmSpeed, LOGIN_PARTICLE_CALM_SPEED_PX, 1, 1000);
        alertSpeedPx = Math.max(
            calmSpeedPx,
            readPositiveInt(stage.dataset.loginParticleAlertSpeed, LOGIN_PARTICLE_ALERT_SPEED_PX, 1, 1000)
        );
        fadeDurationMs = readPositiveInt(stage.dataset.loginParticleFadeDuration, LOGIN_PARTICLE_FADE_DURATION_MS, 1, 60000);
    }

    function clearVariantTimer() {
        if (revertTimer) {
            window.clearTimeout(revertTimer);
            revertTimer = 0;
        }
    }

    function scheduleVariantRevert(delayMs) {
        clearVariantTimer();
        if (currentVariant === baseVariant || delayMs <= 0) {
            return;
        }
        revertTimer = window.setTimeout(() => {
            setVariant(baseVariant, { syncStage: true, revertDelay: 0 });
        }, delayMs);
    }

    function startVisualTransition(nextVariant, durationMs = getTransitionDuration(nextVariant)) {
        paletteFrom = clonePalette(activePalette);
        paletteTo = clonePalette(readPalette(nextVariant));
        speedFrom = activeSpeedMultiplier;
        speedTo = getVariantSpeedMultiplier(nextVariant);
        transitionStartedAt = performance.now();
        transitionDuration = durationMs;
    }

    function updateVisualTransition(now = performance.now()) {
        if (!transitionDuration) {
            return;
        }
        const progress = clamp((now - transitionStartedAt) / transitionDuration, 0, 1);
        activePalette = mixPalette(paletteFrom, paletteTo, progress);
        activeSpeedMultiplier = speedFrom + (speedTo - speedFrom) * progress;
        if (progress >= 1) {
            activePalette = clonePalette(paletteTo);
            activeSpeedMultiplier = speedTo;
            transitionDuration = 0;
        }
    }

    function setVariant(variant, { syncStage = true, revertDelay } = {}) {
        currentVariant = supportedVariants.has(variant) ? variant : baseVariant;
        if (syncStage) {
            stage.dataset.loginParticleVariant = currentVariant;
        }
        const normalizedDelay = typeof revertDelay === "number" ? Math.max(0, revertDelay) : readStageRevertDelay();
        if (syncStage) {
            stage.dataset.loginParticleRevertDelay = normalizedDelay > 0 ? String(normalizedDelay) : "0";
        }
        startVisualTransition(currentVariant);
        scheduleVariantRevert(normalizedDelay);
        renderFrame(performance.now());
        syncAnimationState();
    }

    function isMobileMode() {
        return mobileQuery.matches || coarsePointerQuery.matches;
    }

    function getParticleCount() {
        return isMobileMode() ? configuredMobileCount : configuredDesktopCount;
    }

    function getLineDistance() {
        return isMobileMode() ? 104 : 148;
    }

    function getPointerDistance() {
        return isMobileMode() ? 132 : 178;
    }

    function normalizeDirection(dirX, dirY) {
        const magnitude = Math.hypot(dirX, dirY);
        if (!Number.isFinite(magnitude) || magnitude < 0.001) {
            const angle = Math.random() * Math.PI * 2;
            return {
                x: Math.cos(angle),
                y: Math.sin(angle),
            };
        }
        return {
            x: dirX / magnitude,
            y: dirY / magnitude,
        };
    }

    function getRandomSpeedFactor() {
        return isMobileMode() ? 0.9 + Math.random() * 0.18 : 0.88 + Math.random() * 0.24;
    }

    function getParticleSeedSnapshot(sourceParticles = particles) {
        if (!width || !height || !sourceParticles.length) {
            return [];
        }
        return sourceParticles.map((particle) => ({
            xRatio: width ? particle.x / width : 0,
            yRatio: height ? particle.y / height : 0,
            dirX: particle.dirX,
            dirY: particle.dirY,
            speedFactor: particle.speedFactor,
            size: particle.size,
            opacity: particle.opacity,
            accent: particle.accent,
        }));
    }

    function buildSeedFromSnapshot(item) {
        const speedFactor = clamp(Number(item?.speedFactor || getRandomSpeedFactor()), 0.4, 1.8);
        let direction = normalizeDirection(Number(item?.dirX), Number(item?.dirY));
        if (!Number.isFinite(Number(item?.dirX)) || !Number.isFinite(Number(item?.dirY))) {
            direction = normalizeDirection(Number(item?.vx), Number(item?.vy));
        }
        return {
            xRatio: clamp(Number(item?.xRatio || 0), 0, 1),
            yRatio: clamp(Number(item?.yRatio || 0), 0, 1),
            dirX: direction.x,
            dirY: direction.y,
            speedFactor,
            size: clamp(Number(item?.size || 1.2), 0.8, 4),
            opacity: clamp(Number(item?.opacity || 0.7), 0.2, 1),
            accent: Boolean(item?.accent),
        };
    }

    function createParticle(seed = null) {
        const mobile = isMobileMode();
        const normalizedSeed = seed ? buildSeedFromSnapshot(seed) : null;
        const direction = normalizedSeed ? { x: normalizedSeed.dirX, y: normalizedSeed.dirY } : normalizeDirection();
        return {
            x: normalizedSeed ? normalizedSeed.xRatio * width : Math.random() * width,
            y: normalizedSeed ? normalizedSeed.yRatio * height : Math.random() * height,
            dirX: direction.x,
            dirY: direction.y,
            speedFactor: normalizedSeed ? normalizedSeed.speedFactor : getRandomSpeedFactor(),
            size: normalizedSeed ? normalizedSeed.size : (mobile ? 1 + Math.random() * 1.3 : 1.05 + Math.random() * 1.7),
            opacity: normalizedSeed ? normalizedSeed.opacity : 0.48 + Math.random() * 0.42,
            accent: normalizedSeed ? normalizedSeed.accent : Math.random() > 0.55,
        };
    }

    function readStoredSnapshot() {
        if (cachedStoredSnapshot !== undefined) {
            return cachedStoredSnapshot;
        }
        try {
            const raw = window.sessionStorage.getItem(persistKey);
            if (!raw) {
                cachedStoredSnapshot = null;
                return cachedStoredSnapshot;
            }
            const payload = JSON.parse(raw);
            if (!payload || !Array.isArray(payload.particles)) {
                cachedStoredSnapshot = null;
                return cachedStoredSnapshot;
            }
            if (Date.now() - Number(payload.savedAt || 0) > persistMaxAgeMs) {
                cachedStoredSnapshot = null;
                return cachedStoredSnapshot;
            }
            cachedStoredSnapshot = payload.particles
                .map((item) => buildSeedFromSnapshot(item))
                .filter((item) => Number.isFinite(item.dirX) && Number.isFinite(item.dirY));
            return cachedStoredSnapshot;
        } catch (error) {
            cachedStoredSnapshot = null;
            return cachedStoredSnapshot;
        }
    }

    function persistParticleState() {
        try {
            if (!width || !height || !particles.length) {
                return;
            }
            const payload = {
                savedAt: Date.now(),
                particles: getParticleSeedSnapshot(),
            };
            window.sessionStorage.setItem(persistKey, JSON.stringify(payload));
        } catch (error) {
            // Silent by design.
        }
    }

    function reconcileParticles(seedParticles = []) {
        const nextCount = getParticleCount();
        const normalizedSeeds = Array.isArray(seedParticles) ? seedParticles : [];
        particles = normalizedSeeds.slice(0, nextCount).map((seed) => createParticle(seed));
        while (particles.length < nextCount) {
            particles.push(createParticle());
        }
    }

    function resizeCanvas() {
        const rect = stage.getBoundingClientRect();
        const previousSeeds = particles.length ? getParticleSeedSnapshot() : readStoredSnapshot() || [];
        const shouldBootstrapFromBase =
            !hasBootstrapped && currentVariant !== baseVariant && readStageRevertDelay() > 0;
        const initialVisualVariant = shouldBootstrapFromBase ? baseVariant : currentVariant;
        width = Math.max(1, Math.round(rect.width));
        height = Math.max(1, Math.round(rect.height));
        dpr = Math.min(window.devicePixelRatio || 1, 1.5);
        readConfiguredValues();
        canvas.width = Math.max(1, Math.round(width * dpr));
        canvas.height = Math.max(1, Math.round(height * dpr));
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
        reconcileParticles(previousSeeds);
        activePalette = clonePalette(readPalette(initialVisualVariant));
        paletteFrom = clonePalette(activePalette);
        paletteTo = clonePalette(activePalette);
        activeSpeedMultiplier = getVariantSpeedMultiplier(initialVisualVariant);
        speedFrom = activeSpeedMultiplier;
        speedTo = activeSpeedMultiplier;
        transitionDuration = 0;
        if (shouldBootstrapFromBase) {
            startVisualTransition(currentVariant);
        }
        hasBootstrapped = true;
        renderFrame(performance.now());
        syncAnimationState();
    }

    function scheduleResize() {
        window.clearTimeout(resizeTimer);
        resizeTimer = window.setTimeout(resizeCanvas, 90);
    }

    function updateParticles(deltaSeconds) {
        for (const particle of particles) {
            const currentSpeed = getBaseParticleSpeedPxPerSecond() * particle.speedFactor * activeSpeedMultiplier;
            particle.x += particle.dirX * currentSpeed * deltaSeconds;
            particle.y += particle.dirY * currentSpeed * deltaSeconds;

            if (particle.x <= 0 || particle.x >= width) {
                particle.dirX *= -1;
                particle.x = Math.min(width, Math.max(0, particle.x));
            }
            if (particle.y <= 0 || particle.y >= height) {
                particle.dirY *= -1;
                particle.y = Math.min(height, Math.max(0, particle.y));
            }
        }
    }

    function renderFrame(timestamp = performance.now()) {
        updateVisualTransition(timestamp);
        const lineDistance = getLineDistance();
        const lineDistanceSquared = lineDistance * lineDistance;
        const pointerDistance = getPointerDistance();
        const pointerDistanceSquared = pointerDistance * pointerDistance;
        const paletteCss = {
            dot: colorToCss(activePalette.dot),
            dotSoft: colorToCss(activePalette.dotSoft),
            line: colorToCss(activePalette.line),
            pointerLine: colorToCss(activePalette.pointerLine),
        };

        context.clearRect(0, 0, width, height);
        context.lineWidth = 1;

        for (let index = 0; index < particles.length; index += 1) {
            const particleA = particles[index];
            for (let innerIndex = index + 1; innerIndex < particles.length; innerIndex += 1) {
                const particleB = particles[innerIndex];
                const dx = particleA.x - particleB.x;
                const dy = particleA.y - particleB.y;
                const distanceSquared = dx * dx + dy * dy;
                if (distanceSquared > lineDistanceSquared) {
                    continue;
                }
                const visibility = 1 - distanceSquared / lineDistanceSquared;
                context.globalAlpha = visibility * 0.9;
                context.strokeStyle = paletteCss.line;
                context.beginPath();
                context.moveTo(particleA.x, particleA.y);
                context.lineTo(particleB.x, particleB.y);
                context.stroke();
            }
        }

        if (pointer.active) {
            for (const particle of particles) {
                const dx = pointer.x - particle.x;
                const dy = pointer.y - particle.y;
                const distanceSquared = dx * dx + dy * dy;
                if (distanceSquared > pointerDistanceSquared) {
                    continue;
                }
                const visibility = 1 - distanceSquared / pointerDistanceSquared;
                context.globalAlpha = visibility * 0.95;
                context.strokeStyle = paletteCss.pointerLine;
                context.beginPath();
                context.moveTo(pointer.x, pointer.y);
                context.lineTo(particle.x, particle.y);
                context.stroke();
            }
        }

        for (const particle of particles) {
            context.globalAlpha = particle.opacity;
            context.fillStyle = particle.accent ? paletteCss.dotSoft : paletteCss.dot;
            context.beginPath();
            context.arc(particle.x, particle.y, particle.size, 0, Math.PI * 2);
            context.fill();
        }

        context.globalAlpha = 1;
    }

    function prefersReducedMotion() {
        return reduceMotionQuery.matches;
    }

    function shouldAnimate() {
        return !prefersReducedMotion() && isDocumentVisible;
    }

    function stopAnimation(renderStatic = true) {
        if (rafId) {
            window.cancelAnimationFrame(rafId);
            rafId = 0;
        }
        lastFrameTime = 0;
        if (renderStatic) {
            renderFrame(performance.now());
        }
    }

    function animationFrame(timestamp) {
        if (!rafId) {
            return;
        }
        rafId = window.requestAnimationFrame(animationFrame);
        if (!shouldAnimate()) {
            return;
        }
        if (!lastFrameTime) {
            lastFrameTime = timestamp;
        }
        const elapsed = timestamp - lastFrameTime;
        if (elapsed < frameInterval) {
            return;
        }
        const deltaSeconds = Math.min(0.12, elapsed / 1000);
        lastFrameTime = timestamp;
        updateParticles(deltaSeconds);
        renderFrame(timestamp);
    }

    function startAnimation() {
        if (!shouldAnimate() || rafId) {
            return;
        }
        lastFrameTime = 0;
        rafId = window.requestAnimationFrame(animationFrame);
    }

    function syncAnimationState() {
        if (shouldAnimate()) {
            startAnimation();
            return;
        }
        stopAnimation(true);
    }

    function setPointerFromEvent(event) {
        if (event.pointerType === "touch") {
            pointer.active = false;
            return;
        }
        const rect = canvas.getBoundingClientRect();
        if (
            event.clientX < rect.left ||
            event.clientX > rect.right ||
            event.clientY < rect.top ||
            event.clientY > rect.bottom
        ) {
            pointer.active = false;
            return;
        }
        pointer.active = true;
        pointer.x = event.clientX - rect.left;
        pointer.y = event.clientY - rect.top;
    }

    function clearPointer() {
        pointer.active = false;
    }

    function bindMediaListener(query, handler) {
        if (typeof query.addEventListener === "function") {
            query.addEventListener("change", handler);
            return;
        }
        if (typeof query.addListener === "function") {
            query.addListener(handler);
        }
    }

    window.addEventListener("resize", scheduleResize, { passive: true });
    window.addEventListener("orientationchange", scheduleResize, { passive: true });
    window.addEventListener("focus", syncAnimationState);
    window.addEventListener("blur", () => {
        clearPointer();
        syncAnimationState();
    });
    window.addEventListener("pagehide", persistParticleState);
    window.addEventListener("beforeunload", persistParticleState);
    window.addEventListener("pointermove", setPointerFromEvent, { passive: true });
    stage.addEventListener("pointermove", setPointerFromEvent, { passive: true });
    stage.addEventListener("pointerleave", clearPointer, { passive: true });
    window.addEventListener("pointercancel", (event) => {
        if (event.pointerType === "touch" || event.pointerType === "pen") {
            clearPointer();
        }
    }, { passive: true });
    window.addEventListener("touchstart", clearPointer, { passive: true });
    document.addEventListener("visibilitychange", () => {
        isDocumentVisible = !document.hidden;
        if (document.hidden) {
            persistParticleState();
            clearPointer();
        }
        syncAnimationState();
    });
    document.addEventListener("hub:theme-changed", () => {
        startVisualTransition(currentVariant, getTransitionDuration(currentVariant));
        renderFrame(performance.now());
    });
    document.addEventListener("hub:login-particles-variant-changed", (event) => {
        baseVariant = readStageVariant("loginParticleBaseVariant", baseVariant);
        const nextVariant = event.detail?.variant || readStageVariant("loginParticleVariant", baseVariant);
        const nextDelay = typeof event.detail?.revertDelay === "number" ? event.detail.revertDelay : readStageRevertDelay();
        setVariant(nextVariant, { syncStage: true, revertDelay: nextDelay });
    });
    bindMediaListener(reduceMotionQuery, syncAnimationState);
    bindMediaListener(mobileQuery, scheduleResize);
    bindMediaListener(coarsePointerQuery, scheduleResize);

    resizeCanvas();
    scheduleVariantRevert(readStageRevertDelay());
    syncAnimationState();
}

document.addEventListener("DOMContentLoaded", () => {
    setupLoginParticles(document);
});
