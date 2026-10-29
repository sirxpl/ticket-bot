(() => {
    'use strict';
    const field = document.getElementById('tr-message-search');
    const messages = [...document.querySelectorAll('[data-tr-message]')];
    if (field && messages.length) {
        document.querySelector('.tr-search').hidden = false;
        const searchable = messages.map(element => ({element, text: element.textContent.toLocaleLowerCase()}));
        const count = document.querySelector('[data-tr-count]');
        const empty = document.querySelector('.tr-search-empty');
        const days = [...document.querySelectorAll('.tr-day')];
        const end = document.querySelector('.tr-end');
        field.addEventListener('input', () => {
            const query = field.value.trim().toLocaleLowerCase();
            let visible = 0;
            searchable.forEach(({element, text}) => {
                element.hidden = !!query && !text.includes(query);
                if (!element.hidden) visible++;
            });
            days.forEach(day => { day.hidden = !!query; });
            empty.hidden = visible !== 0;
            end.hidden = visible === 0;
            count.textContent = query ? `${visible} of ${messages.length} messages` : `${messages.length} messages`;
        });
    }
    const print = document.querySelector('.tr-print-button');
    if (print) {
        print.hidden = false;
        print.addEventListener('click', () => window.print());
    }
    document.querySelectorAll('.tr-avatar img').forEach(avatar => {
        const fallback = () => { avatar.hidden = true; };
        avatar.addEventListener('error', fallback);
        if (avatar.complete && !avatar.naturalWidth) fallback();
    });
})();
