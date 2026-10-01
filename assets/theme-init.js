(function () {
    function hydrateTheme() {
        const toggle = document.getElementById("theme-toggle");
        const dash = window.dash_clientside;
        if (!toggle || toggle.dataset.themeHydrated || !dash || !dash.set_props) return;
        toggle.dataset.themeHydrated = "true";
        const theme = localStorage.getItem("utopia-theme-mode") === "dark" ? "dark" : "light";
        dash.set_props("theme-mode", {data: theme});
    }

    const observer = new MutationObserver(hydrateTheme);
    observer.observe(document.documentElement, {childList: true, subtree: true});
    document.addEventListener("DOMContentLoaded", hydrateTheme, {once: true});
    requestAnimationFrame(hydrateTheme);
})();
