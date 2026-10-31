document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("[data-points-selector]").forEach((selector) => {
        const select = selector.querySelector("select");
        const addButton = selector.querySelector("[data-selector-add]");
        const selected = selector.querySelector("[data-selector-selected]");
        const inputName = selector.dataset.inputName;
        if (!select || !addButton || !selected || !inputName) return;

        const addSelectedOption = () => {
            const option = select.options[select.selectedIndex];
            if (!option || !option.value) return;
            const alreadyAdded = Array.from(selected.querySelectorAll('input[name="' + inputName + '"]'))
                .some((input) => input.value === option.value);
            if (alreadyAdded) {
                select.value = "";
                return;
            }
            const item = document.createElement("span");
            item.className = "points-admin-selected-item";
            const hidden = document.createElement("input");
            hidden.type = "hidden";
            hidden.name = inputName;
            hidden.value = option.value;
            const label = document.createElement("span");
            label.textContent = option.textContent;
            const remove = document.createElement("button");
            remove.type = "button";
            remove.textContent = "×";
            remove.setAttribute("data-selector-remove", "");
            remove.setAttribute("aria-label", "Remove " + option.textContent.trim());
            item.append(hidden, label, remove);
            selected.appendChild(item);
            select.value = "";
        };
        addButton.addEventListener("click", addSelectedOption);
        select.addEventListener("change", () => {
            if (select.value) addSelectedOption();
        });
        selected.addEventListener("click", (event) => {
            const remove = event.target.closest("[data-selector-remove]");
            if (remove) remove.closest(".points-admin-selected-item")?.remove();
        });
    });

    const rows = document.getElementById("points-gamemodes");
    const addButton = document.getElementById("points-add-gamemode");
    if (rows && addButton) {
        addButton.addEventListener("click", () => {
            const row = document.createElement("div");
            row.className = "points-admin-gamemode-row";
            const fields = [
                ["gamemode_text", "Gamemode", "text", 80],
                ["gamemode_points", "Points", "number", 0],
                ["gamemode_reason", "Optional reason", "text", 500]
            ];
            for (const [name, placeholder, type, limit] of fields) {
                const input = document.createElement("input");
                input.name = name; input.type = type; input.placeholder = placeholder;
                input.setAttribute("aria-label", placeholder);
                if (type === "number") { input.min = "0"; input.max = "1000000000"; input.step = "1"; }
                else input.maxLength = limit;
                row.appendChild(input);
            }
            const remove = document.createElement("button");
            remove.type = "button"; remove.className = "points-admin-remove";
            remove.textContent = "Remove"; remove.setAttribute("aria-label", "Remove gamemode");
            row.appendChild(remove); rows.appendChild(row); row.querySelector("input")?.focus();
        });
        rows.addEventListener("click", (event) => {
            const remove = event.target.closest(".points-admin-remove");
            if (remove) remove.closest(".points-admin-gamemode-row")?.remove();
        });
    }
    const userInput = document.getElementById("points-adjust-user");
    const nameInput = document.getElementById("points-adjust-name");
    document.querySelectorAll("[data-points-user]").forEach((button) => {
        button.addEventListener("click", () => {
            if (userInput) userInput.value = button.dataset.pointsUser || "";
            if (nameInput) nameInput.value = button.dataset.pointsName || "";
            document.getElementById("points-manager")?.scrollIntoView({behavior: "smooth", block: "center"});
            userInput?.focus();
        });
    });
});