(() => {
    const form = document.querySelector('[data-create-tournament]');
    if (!form) return;
    const type = form.elements.namedItem('tipo');
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
    const cancelTransition = (group) => {
        if (typeof group.getAnimations === 'function') group.getAnimations().forEach((animation) => animation.cancel());
    };
    reducedMotion.addEventListener('change', () => {
        if (reducedMotion.matches) form.querySelectorAll('[data-types]').forEach(cancelTransition);
    });
    const selectedText = (name, fallback = 'Por elegir') => {
        const field = form.elements.namedItem(name);
        return field?.value ? field.selectedOptions?.[0]?.textContent || field.value : fallback;
    };
    const formatDate = (name) => {
        const value = form.elements.namedItem(name).value;
        if (!value) return 'Sin programar';
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat('es-ES', {dateStyle: 'medium', timeStyle: 'short'}).format(date);
    };
    const updateSummary = () => {
        const values = {
            nombre: form.elements.namedItem('nombre').value.trim() || 'Nuevo torneo',
            videojuego: selectedText('videojuego'),
            tipo: selectedText('tipo', 'Por definir'),
            formato: selectedText('formato_competitivo'),
            plazas: form.elements.namedItem('max_participantes').value ? `${form.elements.namedItem('max_participantes').value} plazas` : 'Plazas por definir',
            apertura: formatDate('fecha_apertura_inscripciones'),
            cierre: formatDate('fecha_cierre_inscripciones'),
            inicio: formatDate('fecha_inicio_prevista'),
        };
        document.querySelectorAll('[data-summary]').forEach((element) => {
            element.textContent = values[element.dataset.summary];
        });
    };
    const updateType = () => {
        form.querySelectorAll('[data-types]').forEach((group) => {
            cancelTransition(group);
            const visible = group.dataset.types.split(' ').includes(type.value) || group.dataset.hasErrors === 'true';
            const wasHidden = group.hidden;
            group.hidden = !visible;
            group.querySelectorAll('input, select, textarea').forEach((field) => { field.disabled = !visible; });
            if (visible && wasHidden && !reducedMotion.matches && typeof group.animate === 'function') {
                group.animate([{opacity: 0}, {opacity: 1}], {duration: 140, easing: 'ease-out'});
            }
        });
        form.querySelectorAll('[data-type-description], [data-access-description]').forEach((element) => {
            element.hidden = (element.dataset.typeDescription || element.dataset.accessDescription) !== type.value;
        });
    };
    form.querySelectorAll('.rk-field-group').forEach((group) => {
        const field = group.querySelector('input, select, textarea');
        const descriptions = [...group.querySelectorAll('[id]')].map((element) => element.id).filter((id) => id !== field.id);
        const existing = (field.getAttribute('aria-describedby') || '').split(' ').filter(Boolean);
        if (descriptions.length) field.setAttribute('aria-describedby', [...new Set([...existing, ...descriptions])].join(' '));
    });
    form.addEventListener('input', updateSummary);
    form.addEventListener('change', () => {
        updateType();
        updateSummary();
    });
    updateType();
    updateSummary();
})();