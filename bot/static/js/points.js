document.addEventListener("DOMContentLoaded", () => {
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