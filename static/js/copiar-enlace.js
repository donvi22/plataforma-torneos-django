document.querySelectorAll('[data-copy-target]').forEach((button) => {
    button.addEventListener('click', async () => {
        const input = document.getElementById(button.dataset.copyTarget);
        if (!input) return;
        input.select();
        try {
            await navigator.clipboard.writeText(input.value);
        } catch (error) {
            document.execCommand('copy');
        }
        const feedback = document.querySelector('[data-copy-feedback]');
        if (feedback) feedback.hidden = false;
    });
});
