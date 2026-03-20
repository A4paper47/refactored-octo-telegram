
# --- Admin whitelist (multiple accounts) ---
async def cmd_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await update.message.reply_text("❌ Owner only")

    if not context.args:
        return await update.message.reply_text("Usage: /admin_add <tg_user_id> [display_name]")

    tg_user_id_raw = context.args[0].strip()
    if not tg_user_id_raw.isdigit():
        return await update.message.reply_text("❌ tg_user_id must be numeric")

    tg_user_id = int(tg_user_id_raw)
    display_name = " ".join(context.args[1:]).strip() or None

    row = AdminTelegramUser.query.filter_by(tg_user_id=tg_user_id).first()
    if row:
        row.active = True
        if display_name:
            row.display_name = display_name
    else:
        db.session.add(AdminTelegramUser(tg_user_id=tg_user_id, display_name=display_name, role="ADMIN", active=True))

    db.session.commit()
    await update.message.reply_text(f"✅ Added admin: {tg_user_id}")


async def cmd_admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await update.message.reply_text("❌ Owner only")

    if not context.args or not context.args[0].strip().isdigit():
        return await update.message.reply_text("Usage: /admin_remove <tg_user_id>")

    tg_user_id = int(context.args[0].strip())
    row = AdminTelegramUser.query.filter_by(tg_user_id=tg_user_id).first()
    if not row:
        return await update.message.reply_text("⚠️ Not found")

    row.active = False
    db.session.commit()
    await update.message.reply_text(f"✅ Removed admin: {tg_user_id}")


async def cmd_admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_owner(update):
        return await update.message.reply_text("❌ Owner only")

    rows = AdminTelegramUser.query.order_by(AdminTelegramUser.active.desc(), AdminTelegramUser.tg_user_id.asc()).all()
    lines = ["👥 *Admin whitelist*", ""]

    if not rows:
        lines.append("(empty)")
    else:
        for r in rows:
            status = "✅" if r.active else "⛔️"
            name = f" - {r.display_name}" if r.display_name else ""
            lines.append(f"{status} `{r.tg_user_id}`{name}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
