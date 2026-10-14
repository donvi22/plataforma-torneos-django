(() => {
    const app = document.querySelector('.rk-app');
    if (!app) return;
    const sidebar = document.getElementById('rk-sidebar');
    const sidebarToggle = document.querySelector('.rk-sidebar-toggle');
    const backdrop = document.querySelector('.rk-backdrop');
    const mobile = window.matchMedia('(max-width: 900px)');
    const profile = document.querySelector('[data-profile-menu]');
    const profileToggle = document.getElementById('rk-profile-toggle');
    const profilePopover = document.getElementById('rk-profile-popover');
    const notificationToggle = document.getElementById('notification-toggle');
    const tournaments = document.querySelector('.rk-tournaments');
    const tournamentsToggle = tournaments.querySelector('summary');
    const submenu = tournaments.querySelector('.rk-subnavigation');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    let targetOpen = tournaments.open;
    let animationVersion = 0;
    let submenuAnimations = [];

    const settleSubmenu = () => {
        animationVersion += 1;
        submenuAnimations.forEach((animation) => animation.cancel());
        submenuAnimations = [];
        tournaments.open = targetOpen;
        tournaments.classList.remove('is-animating', 'is-closing');
        tournamentsToggle.setAttribute('aria-expanded', String(targetOpen));
        submenu.inert = !targetOpen;
    };
    const animateSubmenu = async (open) => {
        const currentHeight = tournaments.getBoundingClientRect().height;
        const currentOpacity = tournaments.open ? getComputedStyle(submenu).opacity : '0';
        targetOpen = open;
        animationVersion += 1;
        const version = animationVersion;
        submenuAnimations.forEach((animation) => animation.cancel());
        submenuAnimations = [];
        if (reducedMotion.matches || typeof tournaments.animate !== 'function') {
            settleSubmenu();
            return;
        }
        if (submenu.contains(document.activeElement)) tournamentsToggle.focus();
        submenu.inert = true;
        tournaments.classList.add('is-animating');
        tournaments.classList.toggle('is-closing', !open);
        tournaments.open = true;
        tournamentsToggle.setAttribute('aria-expanded', String(open));
        const expandedHeight = tournaments.getBoundingClientRect().height;
        const collapsedHeight = tournamentsToggle.getBoundingClientRect().height;
        const animate = async (element, frames, duration) => {
            const animation = element.animate(frames, {duration, easing: 'cubic-bezier(0.2, 0, 0.2, 1)', fill: 'both'});
            submenuAnimations.push(animation);
            await animation.finished;
        };
        try {
            if (open) {
                await animate(tournaments, [{height: `${currentHeight}px`}, {height: `${expandedHeight}px`}], 180);
                if (version !== animationVersion) return;
                await animate(submenu, [{opacity: 0}, {opacity: 1}], 90);
            } else {
                await animate(submenu, [{opacity: currentOpacity}, {opacity: 0}], 70);
                if (version !== animationVersion) return;
                await animate(tournaments, [{height: `${currentHeight}px`}, {height: `${collapsedHeight}px`}], 160);
            }
        } catch (error) {
            if (error.name !== 'AbortError') throw error;
        } finally {
            if (version === animationVersion) settleSubmenu();
        }
    };
    tournamentsToggle.addEventListener('click', (event) => {
        event.preventDefault();
        animateSubmenu(!targetOpen);
    });
    reducedMotion.addEventListener('change', settleSubmenu);
    window.addEventListener('resize', settleSubmenu);
    settleSubmenu();

    const closeProfile = (restoreFocus = false) => {
        profilePopover.hidden = true;
        profileToggle.setAttribute('aria-expanded', 'false');
        if (restoreFocus) profileToggle.focus();
    };
    const updateSidebar = () => {
        const open = mobile.matches ? app.classList.contains('rk-sidebar-open') : !app.classList.contains('rk-sidebar-collapsed');
        sidebarToggle.setAttribute('aria-expanded', String(open));
        sidebarToggle.setAttribute('aria-label', open ? 'Contraer menú lateral' : 'Abrir menú lateral');
        backdrop.hidden = !(mobile.matches && open);
        sidebar.inert = !open;
    };
    const closeMobileSidebar = (restoreFocus = false) => {
        app.classList.remove('rk-sidebar-open');
        updateSidebar();
        if (restoreFocus) sidebarToggle.focus();
    };

    document.querySelectorAll('.rk-navigation a').forEach((link) => {
        if (new URL(link.href).pathname === window.location.pathname) link.setAttribute('aria-current', 'page');
    });
    sidebarToggle.addEventListener('click', () => {
        app.classList.toggle(mobile.matches ? 'rk-sidebar-open' : 'rk-sidebar-collapsed');
        closeProfile();
        updateSidebar();
        if (mobile.matches && app.classList.contains('rk-sidebar-open')) sidebar.querySelector('a').focus();
    });
    backdrop.addEventListener('click', () => closeMobileSidebar(true));
    mobile.addEventListener('change', () => {
        app.classList.remove('rk-sidebar-open', 'rk-sidebar-collapsed');
        updateSidebar();
    });
    profileToggle.addEventListener('click', () => {
        const open = profilePopover.hidden;
        if (open && notificationToggle.getAttribute('aria-expanded') === 'true') notificationToggle.click();
        profilePopover.hidden = !open;
        profileToggle.setAttribute('aria-expanded', String(open));
    });
    profileToggle.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            if (profilePopover.hidden) profileToggle.click();
            profilePopover.querySelector('a').focus();
        }
    });
    notificationToggle.addEventListener('click', () => closeProfile());
    document.addEventListener('click', (event) => {
        if (!profile.contains(event.target)) closeProfile();
    });
    document.addEventListener('focusin', (event) => {
        if (!profile.contains(event.target)) closeProfile();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            if (!profilePopover.hidden) closeProfile(true);
            if (app.classList.contains('rk-sidebar-open')) closeMobileSidebar(true);
        }
        if (event.key === 'Tab' && mobile.matches && app.classList.contains('rk-sidebar-open')) {
            const links = [...sidebar.querySelectorAll('a, summary')].filter((element) => element.getClientRects().length);
            const first = links[0];
            const last = links[links.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
    });
    updateSidebar();
})();