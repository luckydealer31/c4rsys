# ruff: noqa: E501 -- the texts are written whole
"""What the bot says, in the three languages of the interface. The language is the one of the
profile; before the Telegram account is bound — the one Telegram names."""

LANGUAGES = ("en", "ru", "th")

TEXTS: dict[str, dict[str, str]] = {
    "not_linked": {
        "ru": "Этот Telegram не привязан к профилю c4rsys. Откройте на сайте «Профиль» → «Telegram» → «Подключить» и перейдите по ссылке оттуда.",
        "en": "This Telegram account is not bound to a c4rsys profile. On the site open «Profile» → «Telegram» → «Connect» and follow the link from there.",
        "th": "บัญชี Telegram นี้ยังไม่ได้ผูกกับโปรไฟล์ c4rsys เปิด «โปรไฟล์» → «Telegram» → «เชื่อมต่อ» บนเว็บไซต์ แล้วเปิดลิงก์จากที่นั่น",
    },
    "code_wrong": {
        "ru": "Ссылка устарела или уже использована. Получите новую в профиле на сайте.",
        "en": "The link is out of date or already used. Get a new one in the profile on the site.",
        "th": "ลิงก์หมดอายุหรือถูกใช้ไปแล้ว รับลิงก์ใหม่ในโปรไฟล์บนเว็บไซต์",
    },
    "share_ask": {
        "ru": "Чтобы привязать Telegram к профилю, подтвердите номер телефона — нажмите кнопку ниже.",
        "en": "To bind Telegram to the profile, confirm the phone number — press the button below.",
        "th": "หากต้องการผูก Telegram กับโปรไฟล์ โปรดยืนยันหมายเลขโทรศัพท์ — กดปุ่มด้านล่าง",
    },
    "share_button": {
        "ru": "Поделиться номером",
        "en": "Share my number",
        "th": "แชร์หมายเลขของฉัน",
    },
    "share_other": {
        "ru": "Нужен ваш собственный номер: нажмите кнопку «Поделиться номером».",
        "en": "Your own number is needed: press the button «Share my number».",
        "th": "ต้องใช้หมายเลขของคุณเอง: กดปุ่ม «แชร์หมายเลขของฉัน»",
    },
    "taken": {
        "ru": "Этот Telegram уже привязан к другому профилю. Сначала отключите его там.",
        "en": "This Telegram account is already bound to another profile. Disconnect it there first.",
        "th": "บัญชี Telegram นี้ผูกกับโปรไฟล์อื่นอยู่แล้ว โปรดยกเลิกการเชื่อมต่อที่นั่นก่อน",
    },
    "linked": {
        "ru": "Готово: Telegram привязан к профилю, номер {phone} подтверждён.",
        "en": "Done: Telegram is bound to the profile, the number {phone} is confirmed.",
        "th": "เรียบร้อย: ผูก Telegram กับโปรไฟล์แล้ว ยืนยันหมายเลข {phone} แล้ว",
    },
    "no_company": {
        "ru": "У вас нет компании, в которой вы работаете.",
        "en": "You have no company you work in.",
        "th": "คุณยังไม่มีบริษัทที่ทำงานอยู่",
    },
    "choose_company": {
        "ru": "Выберите компанию:",
        "en": "Choose a company:",
        "th": "เลือกบริษัท:",
    },
    "menu": {
        "ru": "{company}",
        "en": "{company}",
        "th": "{company}",
    },
    "menu_vehicle": {
        "ru": "Добавить Т/С",
        "en": "Add a vehicle",
        "th": "เพิ่มรถ",
    },
    "menu_invoice": {
        "ru": "Накладная",
        "en": "Delivery note",
        "th": "ใบส่งของ",
    },
    "no_right_receive": {
        "ru": "В этой компании нет склада, на который вы можете принимать товар.",
        "en": "There is no warehouse in this company you can receive goods into.",
        "th": "ไม่มีคลังสินค้าในบริษัทนี้ที่คุณรับสินค้าเข้าได้",
    },
    "which_warehouse": {
        "ru": "На какой склад поступление?",
        "en": "Which warehouse is the receipt for?",
        "th": "รับสินค้าเข้าคลังใด?",
    },
    "send_invoice": {
        "ru": "Склад: {warehouse}. Пришлите фото накладной, счёта или чека поставщика. Можно фото или файл (PDF, картинка).",
        "en": "Warehouse: {warehouse}. Send a photo of the delivery note, the invoice or the receipt of the supplier. A photo or a file (PDF, picture) will do.",
        "th": "คลัง: {warehouse} ส่งรูปถ่ายใบส่งของ ใบแจ้งหนี้ หรือใบเสร็จของผู้ขาย ส่งเป็นรูปถ่ายหรือไฟล์ (PDF, รูปภาพ) ได้",
    },
    "invoice_made": {
        "ru": "Черновик поступления № {number} создан. Строк добавлено: {added} из {total}. Поставщик: {supplier}.\nПроверьте и проведите на сайте:\n{url}",
        "en": "The draft of receipt No. {number} is made. Lines added: {added} of {total}. Supplier: {supplier}.\nCheck and post it on the site:\n{url}",
        "th": "สร้างร่างใบรับสินค้าเลขที่ {number} แล้ว เพิ่มรายการ: {added} จาก {total} ผู้ขาย: {supplier}\nตรวจสอบและบันทึกบนเว็บไซต์:\n{url}",
    },
    "invoice_no_supplier": {
        "ru": "не найден в базе, выберите на сайте",
        "en": "not found in the base, choose on the site",
        "th": "ไม่พบในฐานข้อมูล โปรดเลือกบนเว็บไซต์",
    },
    "invoice_missed": {
        "ru": "Не добавлены (нет в каталоге или не прочитано количество) — добавьте на сайте:\n{missed}",
        "en": "Not added (not in the catalog, or the quantity is not read) — add them on the site:\n{missed}",
        "th": "ไม่ได้เพิ่ม (ไม่มีในแค็ตตาล็อกหรืออ่านจำนวนไม่ได้) — โปรดเพิ่มบนเว็บไซต์:\n{missed}",
    },
    "invoice_nothing": {
        "ru": "Ни одна строка накладной не найдена в каталоге, черновик не создан. Оформите поступление на сайте.\n{missed}",
        "en": "No line of the document is found in the catalog, no draft is made. Make the receipt on the site.\n{missed}",
        "th": "ไม่พบรายการใดของเอกสารในแค็ตตาล็อก จึงไม่ได้สร้างร่าง โปรดทำใบรับสินค้าบนเว็บไซต์\n{missed}",
    },
    "invoice_failed": {
        "ru": "Не удалось создать черновик поступления ({code}). Оформите его на сайте.",
        "en": "The draft of the receipt could not be made ({code}). Make it on the site.",
        "th": "ไม่สามารถสร้างร่างใบรับสินค้าได้ ({code}) โปรดทำบนเว็บไซต์",
    },
    "menu_expense": {"ru": "Чек", "en": "Receipt of an expense", "th": "ใบเสร็จค่าใช้จ่าย"},
    "send_receipt": {
        "ru": "Пришлите фото чека. Можно фото или файл (PDF, картинка).",
        "en": "Send a photo of the receipt. A photo or a file (PDF, picture) will do.",
        "th": "ส่งรูปถ่ายใบเสร็จ ส่งเป็นรูปถ่ายหรือไฟล์ (PDF, รูปภาพ) ได้",
    },
    "receipt_made": {
        "ru": "Черновик расхода создан.\nДата: {date}\nСумма: {amount}\nПродавец: {seller}\nЗа что: {purpose}\nПроверьте и проведите на сайте:\n{url}",
        "en": "The draft of the expense is made.\nDate: {date}\nAmount: {amount}\nSeller: {seller}\nFor: {purpose}\nCheck and record it on the site:\n{url}",
        "th": "สร้างร่างค่าใช้จ่ายแล้ว\nวันที่: {date}\nจำนวนเงิน: {amount}\nผู้ขาย: {seller}\nรายการ: {purpose}\nตรวจสอบและบันทึกบนเว็บไซต์:\n{url}",
    },
    "menu_app": {"ru": "Открыть c4rsys", "en": "Open c4rsys", "th": "เปิด c4rsys"},
    "menu_company": {
        "ru": "Сменить компанию",
        "en": "Another company",
        "th": "เปลี่ยนบริษัท",
    },
    "no_right": {
        "ru": "В этой компании у вас нет права создавать ТС.",
        "en": "You have no right to create vehicles in this company.",
        "th": "คุณไม่มีสิทธิ์สร้างรถในบริษัทนี้",
    },
    "off": {
        "ru": "Чтение документов сейчас не подключено.",
        "en": "Reading of documents is not connected now.",
        "th": "ยังไม่ได้เชื่อมต่อการอ่านเอกสาร",
    },
    "send_photo": {
        "ru": "Пришлите фото документа на ТС — свидетельства о регистрации, blue book или другого с данными машины. Можно фото или файл (PDF, картинка).",
        "en": "Send a photo of a paper of the vehicle — a registration certificate, a blue book or another one with its data. A photo or a file (PDF, picture) will do.",
        "th": "ส่งรูปถ่ายเอกสารรถ เช่น ใบจดทะเบียน เล่มทะเบียน หรือเอกสารอื่นที่มีข้อมูลรถ ส่งเป็นรูปถ่ายหรือไฟล์ (PDF, รูปภาพ) ได้",
    },
    "reading": {
        "ru": "Читаю документ…",
        "en": "Reading the document…",
        "th": "กำลังอ่านเอกสาร…",
    },
    "read_failed": {
        "ru": "Не удалось прочитать документ. Попробуйте другое фото или добавьте ТС на сайте.",
        "en": "The document could not be read. Try another photo or add the vehicle on the site.",
        "th": "ไม่สามารถอ่านเอกสารได้ ลองรูปอื่นหรือเพิ่มรถบนเว็บไซต์",
    },
    "file_wrong": {
        "ru": "Этот файл не подходит: нужны фото (JPEG, PNG, WebP) или PDF до 10 МБ.",
        "en": "This file will not do: a photo (JPEG, PNG, WebP) or a PDF up to 10 MB is needed.",
        "th": "ไฟล์นี้ใช้ไม่ได้: ต้องเป็นรูปถ่าย (JPEG, PNG, WebP) หรือ PDF ไม่เกิน 10 MB",
    },
    "read_title": {
        "ru": "Прочитано из документа:",
        "en": "Read from the document:",
        "th": "อ่านได้จากเอกสาร:",
    },
    "read_ask": {
        "ru": "Проверьте данные. Создать ТС сейчас или открыть заполненную форму на сайте?",
        "en": "Check the data. Create the vehicle now, or open the filled form on the site?",
        "th": "ตรวจสอบข้อมูล สร้างรถเลยหรือเปิดแบบฟอร์มที่กรอกแล้วบนเว็บไซต์?",
    },
    "create_now": {"ru": "Создать сейчас", "en": "Create now", "th": "สร้างเลย"},
    "check_on_site": {
        "ru": "Проверить на сайте",
        "en": "Check on the site",
        "th": "ตรวจสอบบนเว็บไซต์",
    },
    "cancel": {"ru": "Отмена", "en": "Cancel", "th": "ยกเลิก"},
    "cancelled": {"ru": "Отменено.", "en": "Cancelled.", "th": "ยกเลิกแล้ว"},
    "whose": {"ru": "Чьё это Т/С?", "en": "Whose vehicle is it?", "th": "รถคันนี้เป็นของใคร?"},
    "rel_own": {"ru": "Компании", "en": "The company's", "th": "ของบริษัท"},
    "rel_managed": {"ru": "В управлении", "en": "Managed", "th": "รับบริหาร"},
    "rel_client": {"ru": "Клиента", "en": "A client's", "th": "ของลูกค้า"},
    "where": {
        "ru": "В какой локации учитывать ТС?",
        "en": "Which location is the vehicle kept in?",
        "th": "บันทึกรถไว้ที่สาขาใด?",
    },
    "no_location": {
        "ru": "Нет локации, в которой вы можете создать ТС.",
        "en": "There is no location you can create a vehicle in.",
        "th": "ไม่มีสาขาที่คุณสร้างรถได้",
    },
    "created": {
        "ru": "ТС создано: {name}\n{url}",
        "en": "The vehicle is created: {name}\n{url}",
        "th": "สร้างรถแล้ว: {name}\n{url}",
    },
    "create_failed": {
        "ru": "Не удалось создать ТС ({code}). Добавьте его на сайте.",
        "en": "The vehicle could not be created ({code}). Add it on the site.",
        "th": "ไม่สามารถสร้างรถได้ ({code}) โปรดเพิ่มบนเว็บไซต์",
    },
    "start_over": {
        "ru": "Начните с меню: /menu",
        "en": "Start from the menu: /menu",
        "th": "เริ่มจากเมนู: /menu",
    },
}

# The names of what is read from a paper.
FIELDS: dict[str, dict[str, str]] = {
    "make": {"ru": "Марка", "en": "Make", "th": "ยี่ห้อ"},
    "model": {"ru": "Модель", "en": "Model", "th": "รุ่น"},
    "year": {"ru": "Год", "en": "Year", "th": "ปี"},
    "plate": {"ru": "Регистрационный номер", "en": "Plate", "th": "ป้ายทะเบียน"},
    "plate_country": {
        "ru": "Страна регистрации",
        "en": "Country of registration",
        "th": "ประเทศที่จดทะเบียน",
    },
    "vin": {"ru": "VIN", "en": "VIN", "th": "VIN"},
    "body_number": {"ru": "Номер кузова", "en": "Body number", "th": "หมายเลขตัวถัง"},
    "chassis_number": {"ru": "Номер шасси", "en": "Chassis number", "th": "หมายเลขแชสซี"},
    "frame_number": {"ru": "Номер рамы", "en": "Frame number", "th": "หมายเลขเฟรม"},
    "engine_number": {"ru": "Номер двигателя", "en": "Engine number", "th": "หมายเลขเครื่องยนต์"},
    "color": {"ru": "Цвет", "en": "Colour", "th": "สี"},
    "body_type": {"ru": "Тип кузова", "en": "Body type", "th": "ประเภทตัวถัง"},
    "fuel_type": {"ru": "Топливо", "en": "Fuel", "th": "เชื้อเพลิง"},
    "transmission": {"ru": "Коробка", "en": "Gearbox", "th": "เกียร์"},
    "drive": {"ru": "Привод", "en": "Drive", "th": "ระบบขับเคลื่อน"},
    "engine_volume_cc": {
        "ru": "Объём двигателя, см³",
        "en": "Engine volume, cc",
        "th": "ปริมาตรเครื่องยนต์ ซีซี",
    },
    "power_hp": {"ru": "Мощность, л. с.", "en": "Power, hp", "th": "กำลัง แรงม้า"},
    "seats": {"ru": "Мест", "en": "Seats", "th": "ที่นั่ง"},
    "doors": {"ru": "Дверей", "en": "Doors", "th": "ประตู"},
    "curb_weight_kg": {
        "ru": "Снаряжённая масса, кг",
        "en": "Curb weight, kg",
        "th": "น้ำหนักรถเปล่า กก.",
    },
    "gross_weight_kg": {"ru": "Полная масса, кг", "en": "Gross weight, kg", "th": "น้ำหนักรวม กก."},
    "owner_name": {
        "ru": "Владелец в документе",
        "en": "Owner in the document",
        "th": "เจ้าของในเอกสาร",
    },
}


def language(code: str | None) -> str:
    short = (code or "")[:2].lower()
    return short if short in LANGUAGES else "en"


def t(lang: str, key: str, **params: object) -> str:
    return TEXTS[key][lang].format(**params)


def field(lang: str, key: str) -> str:
    return FIELDS[key][lang]
