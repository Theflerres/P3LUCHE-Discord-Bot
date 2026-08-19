from src.p3luche.main import bot, TOKEN

if __name__ == "__main__":
    try:
        bot.run(TOKEN)
    except Exception as e:
        print(f"Erro fatal ao iniciar: {e}")