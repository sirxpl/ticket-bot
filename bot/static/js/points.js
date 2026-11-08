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
        const makeTier = (gamemodeName = "") => {
            const row = document.createElement("div");
            row.className = "points-admin-gamemode-row";
            row.setAttribute("data-gamemode-tier", "");
            const hidden = document.createElement("input");
            hidden.type = "hidden"; hidden.name = "gamemode_text"; hidden.value = gamemodeName;
            row.appendChild(hidden);
            const fields = [
                ["gamemode_min_players", "Players or more", "number"],
                ["gamemode_points", "Points (+/- or decimal)", "number"],
                ["gamemode_reason", "Optional reason", "text"]
            ];
            for (const [name, placeholder, type] of fields) {
                const input = document.createElement("input");
                input.name = name; input.type = type; input.placeholder = placeholder;
                input.setAttribute("aria-label", placeholder);
                if (name === "gamemode_min_players") { input.min = "1"; input.max = "1000"; input.step = "1"; input.value = "1"; }
                else if (type === "number") { input.min = "-1000000000"; input.max = "1000000000"; input.step = "any"; }
                else input.maxLength = 500;
                row.appendChild(input);
            }
            const remove = document.createElement("button");
            remove.type = "button"; remove.className = "points-admin-remove";
            remove.textContent = "Remove tier"; remove.setAttribute("data-remove-tier", "");
            row.appendChild(remove);
            return row;
        };
        const makeGroup = (name = "") => {
            const group = document.createElement("div");
            group.className = "points-admin-gamemode-group";
            group.setAttribute("data-gamemode-group", "");
            const heading = document.createElement("div");
            heading.className = "points-admin-gamemode-heading";
            const input = document.createElement("input");
            input.type = "text"; input.maxLength = 80; input.placeholder = "Gamemode";
            input.setAttribute("data-gamemode-name", ""); input.setAttribute("aria-label", "Gamemode name");
            input.value = name;
            const remove = document.createElement("button");
            remove.type = "button"; remove.className = "points-admin-remove";
            remove.textContent = "Remove gamemode"; remove.setAttribute("data-remove-gamemode", "");
            heading.append(input, remove);
            const tiers = document.createElement("div");
            tiers.className = "points-admin-reward-tiers"; tiers.setAttribute("data-gamemode-tiers", "");
            tiers.appendChild(makeTier(name));
            const addTier = document.createElement("button");
            addTier.type = "button"; addTier.className = "points-admin-add";
            addTier.textContent = "＋ Add player-count tier"; addTier.setAttribute("data-add-tier", "");
            group.append(heading, tiers, addTier);
            return group;
        };
        const syncNames = () => {
            rows.querySelectorAll("[data-gamemode-group]").forEach((group) => {
                const name = group.querySelector("[data-gamemode-name]")?.value || "";
                group.querySelectorAll('input[name="gamemode_text"]').forEach((hidden) => { hidden.value = name; });
            });
        };
        rows.addEventListener("input", (event) => {
            if (event.target.matches("[data-gamemode-name]")) syncNames();
        });
        rows.addEventListener("click", (event) => {
            const addTier = event.target.closest("[data-add-tier]");
            if (addTier) {
                const group = addTier.closest("[data-gamemode-group]");
                const name = group.querySelector("[data-gamemode-name]")?.value || "";
                const tier = makeTier(name);
                group.querySelector("[data-gamemode-tiers]").appendChild(tier);
                tier.querySelector('input[name="gamemode_min_players"]')?.focus();
                return;
            }
            const removeTier = event.target.closest("[data-remove-tier]");
            if (removeTier) {
                const tierGroup = removeTier.closest("[data-gamemode-tiers]");
                if (tierGroup.querySelectorAll("[data-gamemode-tier]").length > 1) {
                    removeTier.closest("[data-gamemode-tier]")?.remove();
                } else {
                    removeTier.closest("[data-gamemode-tier]")?.querySelectorAll("input:not([type=hidden])").forEach((input) => { input.value = ""; });
                }
                return;
            }
            const removeGroup = event.target.closest("[data-remove-gamemode]");
            if (removeGroup) removeGroup.closest("[data-gamemode-group]")?.remove();
        });
        addButton.addEventListener("click", () => {
            const group = makeGroup();
            rows.appendChild(group);
            group.querySelector("[data-gamemode-name]")?.focus();
        });
        rows.closest("form")?.addEventListener("submit", syncNames);
    }
    const phraseRows = document.getElementById("points-phrase-rewards");
    const addPhraseButton = document.getElementById("points-add-phrase-reward");
    if (phraseRows && addPhraseButton) {
        addPhraseButton.addEventListener("click", () => {
            const row = document.createElement("div");
            row.className = "points-admin-gamemode-row";
            const fields = [
                ["phrase_reward_text", "Phrase to detect", "text", 80],
                ["phrase_reward_points", "Points", "number", 0],
                ["phrase_reward_reason", "Optional reason", "text", 500]
            ];
            for (const [name, placeholder, type, limit] of fields) {
                const input = document.createElement("input");
                input.name = name; input.type = type; input.placeholder = placeholder;
                input.setAttribute("aria-label", placeholder);
                if (type === "number") { input.min = "-1000000000"; input.max = "1000000000"; input.step = "any"; }
                else input.maxLength = limit;
                row.appendChild(input);
            }
            const remove = document.createElement("button");
            remove.type = "button"; remove.className = "points-admin-remove";
            remove.textContent = "Remove"; remove.setAttribute("aria-label", "Remove phrase reward");
            row.appendChild(remove); phraseRows.appendChild(row); row.querySelector("input")?.focus();
        });
        phraseRows.addEventListener("click", (event) => {
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