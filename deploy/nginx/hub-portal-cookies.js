const PORTAL_SESSION_COOKIE = "hub_portal_session";

function parseCookies(headerValue) {
    return String(headerValue || "")
        .split(";")
        .map((chunk) => chunk.trim())
        .filter(Boolean);
}

function portalGateCookie(r) {
    const cookies = parseCookies(r.headersIn.Cookie);
    return cookies.filter((cookie) => cookie.startsWith(PORTAL_SESSION_COOKIE + "=")).join("; ");
}

function upstreamCookie(r) {
    const cookies = parseCookies(r.headersIn.Cookie);
    return cookies.filter((cookie) => !cookie.startsWith(PORTAL_SESSION_COOKIE + "=")).join("; ");
}

export default { portalGateCookie, upstreamCookie };
