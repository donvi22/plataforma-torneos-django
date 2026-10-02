(() => {
	const menu = document.querySelector('[data-notification-menu]');
	const toggle = document.getElementById('notification-toggle');
	const popover = document.getElementById('notification-popover');

	if (!menu || !toggle || !popover) {
		return;
	}

	const setOpen = (open, restoreFocus = false) => {
		popover.hidden = !open;
		toggle.setAttribute('aria-expanded', String(open));
		if (!open && restoreFocus) {
			toggle.focus();
		}
	};

	toggle.addEventListener('click', () => {
		setOpen(popover.hidden);
	});

	document.addEventListener('click', (event) => {
		if (!popover.hidden && !menu.contains(event.target)) {
			setOpen(false);
		}
	});

	document.addEventListener('keydown', (event) => {
		if (event.key === 'Escape' && !popover.hidden) {
			setOpen(false, true);
		}
	});
})();