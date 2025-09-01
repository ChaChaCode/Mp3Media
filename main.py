import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import os
from mutagen.mp3 import MP3
from mutagen.id3 import APIC, TIT2, TPE1
from PIL import Image
import io

# Настройки
BOT_TOKEN = "ТВОЙ-ТОКЕН-БОТА"
TEMP_DIR = "temp_files"

# Создаем папку для временных файлов
os.makedirs(TEMP_DIR, exist_ok=True)


# Состояния бота
class MusicStates(StatesGroup):
	waiting_for_photo = State()
	waiting_for_title = State()
	waiting_for_artist = State()


# Инициализация
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Словарь для хранения данных пользователей
user_data = {}


@dp.message(Command("start"))
async def cmd_start(message: Message):
	await message.answer("Скинь MP3 файл, я добавлю к нему обложку, название и исполнителя!")


@dp.message(lambda message: message.audio or message.document)
async def handle_music(message: Message, state: FSMContext):
	# Получаем файл
	if message.audio:
		file = message.audio
		file_name = f"{file.file_id}.mp3"
	elif message.document and message.document.mime_type == "audio/mpeg":
		file = message.document
		file_name = message.document.file_name or f"{file.file_id}.mp3"
	else:
		await message.answer("Пришли MP3 файл")
		return

	# Скачиваем файл
	file_path = os.path.join(TEMP_DIR, file_name)
	file_info = await bot.get_file(file.file_id)
	await bot.download_file(file_info.file_path, file_path)

	# Сохраняем путь к файлу
	user_data[message.from_user.id] = {
		'music_path': file_path,
		'original_name': file_name
	}

	await state.set_state(MusicStates.waiting_for_photo)
	await message.answer("Теперь скинь фотографию для обложки")


@dp.message(MusicStates.waiting_for_photo)
async def handle_photo(message: Message, state: FSMContext):
	if not message.photo:
		await message.answer("Скинь фотографию!")
		return

	user_id = message.from_user.id
	if user_id not in user_data:
		await message.answer("Сначала скинь MP3 файл")
		return

	# Скачиваем фото
	photo = message.photo[-1]  # Берем самое большое разрешение
	photo_path = os.path.join(TEMP_DIR, f"{photo.file_id}.jpg")
	file_info = await bot.get_file(photo.file_id)
	await bot.download_file(file_info.file_path, photo_path)

	# Обрабатываем изображение (сжимаем если нужно)
	with Image.open(photo_path) as img:
		# Конвертируем в RGB если нужно
		if img.mode != 'RGB':
			img = img.convert('RGB')

		# Ресайзим до разумного размера (500x500 max)
		img.thumbnail((500, 500), Image.Resampling.LANCZOS)

		# Сохраняем в байты
		img_bytes = io.BytesIO()
		img.save(img_bytes, format='JPEG', quality=90)
		img_data = img_bytes.getvalue()

	# Сохраняем данные фото
	user_data[user_id]['photo_data'] = img_data
	user_data[user_id]['photo_path'] = photo_path

	await state.set_state(MusicStates.waiting_for_title)
	await message.answer("Теперь напиши название трека")


@dp.message(MusicStates.waiting_for_title)
async def handle_title(message: Message, state: FSMContext):
	if not message.text:
		await message.answer("Напиши текстом название трека!")
		return

	user_id = message.from_user.id
	if user_id not in user_data:
		await message.answer("Что-то пошло не так, начни сначала")
		return

	title = message.text.strip()
	user_data[user_id]['title'] = title

	await state.set_state(MusicStates.waiting_for_artist)
	await message.answer("Теперь напиши исполнителя")


@dp.message(MusicStates.waiting_for_artist)
async def handle_artist(message: Message, state: FSMContext):
	if not message.text:
		await message.answer("Напиши текстом имя исполнителя!")
		return

	user_id = message.from_user.id
	if user_id not in user_data:
		await message.answer("Что-то пошло не так, начни сначала")
		return

	artist = message.text.strip()
	title = user_data[user_id]['title']
	music_path = user_data[user_id]['music_path']
	img_data = user_data[user_id]['photo_data']
	new_path = None  # Инициализируем переменную

	try:
		# Загружаем MP3
		audiofile = MP3(music_path)

		# Добавляем ID3 теги если их нет
		if audiofile.tags is None:
			audiofile.add_tags()

		# Добавляем название трека
		audiofile.tags.add(TIT2(encoding=3, text=title))

		# Добавляем исполнителя
		audiofile.tags.add(TPE1(encoding=3, text=artist))

		# Добавляем обложку
		audiofile.tags.add(
			APIC(
				encoding=3,  # UTF-8
				mime='image/jpeg',
				type=3,  # Cover (front)
				desc='Cover',
				data=img_data
			)
		)

		# Сохраняем
		audiofile.save()

		# Создаем новое имя файла: "Исполнитель - Название.mp3"
		safe_artist = "".join(c for c in artist if c.isalnum() or c in (' ', '-', '_')).rstrip()
		safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()

		if safe_artist and safe_title:
			new_filename = f"{safe_artist} - {safe_title}.mp3"
		elif safe_title:
			new_filename = f"{safe_title}.mp3"
		else:
			new_filename = user_data[user_id]['original_name']

		new_path = os.path.join(TEMP_DIR, new_filename)

		# Переименовываем файл
		os.rename(music_path, new_path)

		# Отправляем файл обратно
		output_file = FSInputFile(new_path, filename=new_filename)
		await message.answer_audio(
			audio=output_file,
			caption=f"""Готово!
🎵 Исполнитель: {artist}
📝 Название: {title}
🎨 Обложка добавлена

💡 Чтобы увидеть обложку:
- Скачай файл и открой в плеере
- Или скинь этот файл обратно в телеграм"""
		)

	except Exception as e:
		await message.answer(f"Ошибка: {str(e)}")

	finally:
		# Очищаем временные файлы
		try:
			if new_path and os.path.exists(new_path):
				os.remove(new_path)
			elif os.path.exists(music_path):
				os.remove(music_path)

			photo_path = user_data[user_id].get('photo_path')
			if photo_path and os.path.exists(photo_path):
				os.remove(photo_path)
		except:
			pass

		# Очищаем данные пользователя
		if user_id in user_data:
			del user_data[user_id]

		await state.clear()


# Обработка отмены
@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
	user_id = message.from_user.id

	# Очищаем файлы
	if user_id in user_data:
		try:
			music_path = user_data[user_id].get('music_path')
			photo_path = user_data[user_id].get('photo_path')

			if music_path and os.path.exists(music_path):
				os.remove(music_path)
			if photo_path and os.path.exists(photo_path):
				os.remove(photo_path)
		except:
			pass

		del user_data[user_id]

	await state.clear()
	await message.answer("Отменено. Можешь начать сначала с нового MP3 файла")


async def main():
	await dp.start_polling(bot)


if __name__ == '__main__':
	asyncio.run(main())