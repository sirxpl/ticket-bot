diff --git a/bot/cogs/tickets.py b/bot/cogs/tickets.py
index cbc2a23..606da60 100644
--- a/bot/cogs/tickets.py
+++ b/bot/cogs/tickets.py
@@ -1105,6 +1105,14 @@ class TicketsCog(commands.Cog):
         # Keep close operations idempotent while transcript generation and
         # channel deletion are still in progress.
         self._closing_ticket_ids = set()
+        # Context-menu decorators cannot be used on methods of a Cog.  Build
+        # and register this command explicitly, with the bound method as its
+        # callback, so discord.py can load this extension.
+        self.toggle_pin_context_menu = app_commands.ContextMenu(
+            name="📌 Toggle Pin",
+            callback=self.toggle_pin_message,
+        )
+        self.bot.tree.add_command(self.toggle_pin_context_menu)
 
     def get_ticket_view(self):
         return TicketView(self.bot)
@@ -1114,6 +1122,10 @@ class TicketsCog(commands.Cog):
 
     def cog_unload(self):
         self.autoclose_watcher.cancel()
+        self.bot.tree.remove_command(
+            self.toggle_pin_context_menu.name,
+            type=self.toggle_pin_context_menu.type,
+        )
 
     @commands.Cog.listener()
     async def on_message(self, message: discord.Message):
@@ -1773,7 +1785,6 @@ class TicketsCog(commands.Cog):
         await interaction.response.send_message(embed=emb, ephemeral=True)
 
     # Message Context Menu: right-click a message -> Apps -> 📌 Toggle Pin
-    @app_commands.context_menu(name="📌 Toggle Pin")
     async def toggle_pin_message(self, interaction: discord.Interaction, message: discord.Message):
         if not await self._pin_command_check(interaction):
             return
