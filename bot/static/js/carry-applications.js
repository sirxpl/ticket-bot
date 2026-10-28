(() => {
    'use strict';
    const root = document.querySelector('[data-application-server-now]');
    if (root) {
        const serverNow = Number(root.dataset.applicationServerNow) * 1000;
        const started = Date.now();
        const clocks = root.querySelectorAll('[data-application-deadline]');
        const tick = () => {
            const now = serverNow + Date.now() - started;
            clocks.forEach(clock => {
                const seconds = Math.max(0, Math.ceil(Number(clock.dataset.applicationDeadline) - now / 1000));
                if (!Number.isFinite(seconds)) return;
                if (!seconds) {
                    clock.textContent = clock.dataset.readyLabel || 'Cooldown ended';
                    return;
                }
                const days = Math.floor(seconds / 86400);
                const hours = Math.floor(seconds % 86400 / 3600);
                const minutes = Math.floor(seconds % 3600 / 60);
                clock.textContent = `${days}d ${hours}h ${minutes}m ${seconds % 60}s`;
            });
        };
        tick();
        if (clocks.length) window.setInterval(tick, 1000);
    }
    const form = document.querySelector('[data-application-selection]');
    if (!form) return;
    const boxes = [...form.querySelectorAll('input[name="application_ids"]')];
    const selectAll = form.querySelector('[data-application-select-all]');
    const update = () => {
        const selected = boxes.filter(box => box.checked);
        selectAll.checked = boxes.length > 0 && selected.length === boxes.length;
        selectAll.indeterminate = selected.length > 0 && selected.length < boxes.length;
        selectAll.disabled = boxes.length === 0;
        form.querySelector('[data-application-selection-count]').textContent = `${selected.length} selected`;
        form.querySelector('[value="remove_accepted"]').disabled = !selected.some(box => box.dataset.applicationStatus === 'accepted');
        form.querySelector('[value="clear_cooldowns"]').disabled = !selected.some(box => box.dataset.applicationStatus === 'denied');
    };
    selectAll.addEventListener('change', () => { boxes.forEach(box => { box.checked = selectAll.checked; }); update(); });
    boxes.forEach(box => box.addEventListener('change', update));
    update();
})();
