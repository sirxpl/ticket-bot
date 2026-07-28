# ---------------------------------------------------------------------------
# CARRY PANEL DEPLOYMENT ROUTE (Add this inside main.py)
# ---------------------------------------------------------------------------
@app.route("/dashboard/tickets", methods=["GET", "POST"])
def ticket_panel_config():
    if request.method == "POST":
        channel_id = int(request.form.get("channel_id", 0))
        category_id = int(request.form.get("category_id", 0)) if request.form.get("category_id") else None
        support_role_id = int(request.form.get("support_role_id", 0)) if request.form.get("support_role_id") else None
        log_channel_id = int(request.form.get("log_channel_id", 0)) if request.form.get("log_channel_id") else None
        title = request.form.get("title", "Request Carry")
        description = request.form.get("description", "Click below to request a carry ticket!")

        # Find channel and send the panel embed directly via the bot
        channel = bot.get_channel(channel_id)
        if channel:
            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.blue()
            )
            # Create persistent button view with settings inside custom_id
            view = CarryPanelView(category_id, support_role_id, log_channel_id)
            
            # Send asynchronously to Discord
            future = asyncio.run_coroutine_threadsafe(
                channel.send(embed=embed, view=view),
                bot.loop
            )
            try:
                future.result(timeout=10)
                flash("🎉 Carry panel successfully deployed to Discord!", "success")
            except Exception as e:
                flash(f"❌ Failed to send panel: {e}", "danger")
        else:
            flash("❌ Discord channel not found.", "danger")

        return redirect("/dashboard/tickets")

    # Fetch choices for dropdowns on GET request
    guild = bot.guilds[0] if bot.guilds else None
    channels = guild.text_channels if guild else []
    categories = guild.categories if guild else []
    roles = guild.roles if guild else []

    return render_template(
        "tickets_config.html",
        channels=channels,
        categories=categories,
        roles=roles
    )
