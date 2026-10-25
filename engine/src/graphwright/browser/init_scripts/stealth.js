// Canvas + WebGL spoofing init script.
//
// Reads its config from window.__rpaStealthConfig, which the BrowserPool sets
// in a preceding init-script with values from the active MachineProfile.
//
// Noise is CONSISTENT within a session — randomizing per call is itself a
// fingerprint. The seed is generated once per context and stays stable for
// that session's lifetime.

(() => {
    const cfg = (typeof window !== 'undefined' && window.__rpaStealthConfig) || {};
    const seed = (cfg.seed | 0) || 7;
    const webglVendor = cfg.webglVendor || 'Intel Inc.';
    const webglRenderer = cfg.webglRenderer || 'Intel Iris OpenGL Engine';

    // --- Canvas: deterministic per-pixel noise based on seed -------------------
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (...args) {
        try {
            const ctx = this.getContext('2d');
            if (ctx && this.width > 0 && this.height > 0) {
                const w = Math.min(this.width, 16);
                const h = Math.min(this.height, 16);
                const img = ctx.getImageData(0, 0, w, h);
                // Mutate a handful of low-order bits using the seed — invisible
                // to the human eye but enough to change the hash deterministically.
                for (let i = 0; i < img.data.length; i += 4) {
                    img.data[i] ^= (seed & 0x01);
                    img.data[i + 1] ^= (seed & 0x02) >> 1;
                    img.data[i + 2] ^= (seed & 0x04) >> 2;
                }
                ctx.putImageData(img, 0, 0);
            }
        } catch (_) { /* readback can fail on tainted canvases — ignore */ }
        return origToDataURL.apply(this, args);
    };

    // --- WebGL: spoof vendor + renderer ---------------------------------------
    const UNMASKED_VENDOR_WEBGL = 37445;
    const UNMASKED_RENDERER_WEBGL = 37446;

    function patchGetParameter(proto) {
        if (!proto || !proto.getParameter) return;
        const orig = proto.getParameter;
        proto.getParameter = function (parameter) {
            if (parameter === UNMASKED_VENDOR_WEBGL) return webglVendor;
            if (parameter === UNMASKED_RENDERER_WEBGL) return webglRenderer;
            return orig.apply(this, [parameter]);
        };
    }
    patchGetParameter(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') {
        patchGetParameter(WebGL2RenderingContext.prototype);
    }
})();
