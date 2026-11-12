"""Thai explanations of the columns of the XLSX templates (ТЗ § 22.2.6; decision Б-7: the
explanations in the three languages of the interface). Keyed by the English explanation of each
column, so one text serves every template that has it. The technical codes of the columns do
not change. PROPOSAL (agent): the Thai texts wait for the check of the customer."""

TH: dict[str, str] = {
    # Vehicles
    "VHL ID of a vehicle already in your garage, to find it": (
        "VHL ID ของรถที่มีอยู่แล้วในโรงรถของคุณ เพื่อค้นหา"
    ),
    "ID of a location of the company (sheet «locations»); required": (
        "ID ของสาขาของบริษัท (แผ่นงาน «locations»); จำเป็น"
    ),
    "own or managed; required": "own (เป็นเจ้าของ) หรือ managed (บริหารจัดการ); จำเป็น",
    "Own name of the card; formed from make, model and plate when empty": (
        "ชื่อของบัตรรถ; หากว่างจะสร้างจากยี่ห้อ รุ่น และทะเบียน"
    ),
    "Odometer unit: km or mi; required": "หน่วยของมาตรวัดระยะทาง: km หรือ mi; จำเป็น",
    "passenger_car, truck, bus, motorcycle, moped, other": (
        "passenger_car, truck, bus, motorcycle, moped, other — ประเภทรถ"
    ),
    "Make: code or name of the catalog, or any name": "ยี่ห้อ: รหัสหรือชื่อจากแคตตาล็อก หรือชื่อใดก็ได้",
    "Model": "รุ่น",
    "Year of manufacture": "ปีที่ผลิต",
    "Registration plate": "ป้ายทะเบียน",
    "Country of the plate, two letters": "ประเทศของป้ายทะเบียน ตัวอักษรสองตัว",
    "VIN": "VIN (หมายเลขตัวถังสากล)",
    "Body number": "หมายเลขตัวถัง",
    "Chassis number": "หมายเลขแชสซี",
    "Frame number": "หมายเลขโครงรถ",
    "Engine number": "หมายเลขเครื่องยนต์",
    "Color": "สี",
    "sedan, hatchback, wagon, suv, crossover, coupe, convertible, minivan, pickup, van, other": (
        "sedan, hatchback, wagon, suv, crossover, coupe, convertible, minivan, pickup, van, other"
        " — ประเภทตัวถัง"
    ),
    "petrol, diesel, hybrid, plug_in_hybrid, electric, gas, other": (
        "petrol, diesel, hybrid, plug_in_hybrid, electric, gas, other — ประเภทเชื้อเพลิง"
    ),
    "manual, automatic, robot, cvt": "manual, automatic, robot, cvt — ระบบเกียร์",
    "fwd, rwd, awd": "fwd, rwd, awd — ระบบขับเคลื่อน",
    "Engine volume, cc": "ความจุเครื่องยนต์ ซีซี",
    "Power, hp": "กำลัง แรงม้า",
    "Seats": "จำนวนที่นั่ง",
    "Doors": "จำนวนประตู",
    "Current odometer reading in the unit of the card": "เลขไมล์ปัจจุบันในหน่วยของบัตรรถ",
    "Allowed usage contexts, comma separated (rental); all applicable when empty": (
        "บริบทการใช้งานที่อนุญาต คั่นด้วยจุลภาค (rental); หากว่างหมายถึงทุกบริบทที่ใช้ได้"
    ),
    "Managed only: profile, person, organization or none": (
        "เฉพาะรถที่บริหารจัดการ: profile, person, organization หรือ none"
    ),
    "Owner's c4rsys ID, email or phone (owner_kind = profile)": (
        "c4rsys ID อีเมล หรือโทรศัพท์ของเจ้าของ (owner_kind = profile)"
    ),
    "Owner's name (person or organization)": "ชื่อเจ้าของ (บุคคลหรือองค์กร)",
    "Owner's contact": "ช่องทางติดต่อเจ้าของ",
    # Clients and counterparties
    "person or company; required": "person (บุคคล) หรือ company (บริษัท); จำเป็น",
    "First name; required for a person": "ชื่อ; จำเป็นสำหรับบุคคล",
    "Last name; required for a person": "นามสกุล; จำเป็นสำหรับบุคคล",
    "Middle name": "ชื่อกลาง",
    "Birth date, YYYY-MM-DD": "วันเกิด YYYY-MM-DD",
    "Name; required for a company": "ชื่อ; จำเป็นสำหรับบริษัท",
    "Phone; at least one contact is required": "โทรศัพท์; ต้องมีช่องทางติดต่ออย่างน้อยหนึ่งช่องทาง",
    "Priority messenger by this phone: whatsapp, telegram, line, wechat, viber, signal, "
    "kakaotalk, zalo, facebook_messenger, other; a call or SMS when empty": (
        "แอปส่งข้อความหลักของหมายเลขนี้: whatsapp, telegram, line, wechat, viber, signal, "
        "kakaotalk, zalo, facebook_messenger, other; หากว่างหมายถึงโทรหรือ SMS"
    ),
    "Second phone": "โทรศัพท์ที่สอง",
    "Messenger by the second phone": "แอปส่งข้อความของหมายเลขที่สอง",
    "Email": "อีเมล",
    "Second email": "อีเมลที่สอง",
    "Address": "ที่อยู่",
    "Comment": "หมายเหตุ",
    "Your own ID of the card, used to find it again": "ID ของบัตรในระบบของคุณ ใช้เพื่อค้นหาอีกครั้ง",
    "c4rsys ID of the person's profile (USR-…); sends no request": (
        "c4rsys ID ของโปรไฟล์บุคคล (USR-…); ไม่ส่งคำขอ"
    ),
    "Registration and tax details": "ข้อมูลการจดทะเบียนและภาษี",
    "Category of the counterparty": "หมวดหมู่ของคู่ค้า",
    # Items
    "Name of the item; required": "ชื่อรายการ; จำเป็น",
    "Unit code: piece, liter, kilogram, meter; required": (
        "รหัสหน่วย: piece, liter, kilogram, meter; จำเป็น"
    ),
    "Article (part number)": "รหัสสินค้า (หมายเลขชิ้นส่วน)",
    "Category": "หมวดหมู่",
    "Manufacturer as named in the catalog, or any name": "ผู้ผลิตตามชื่อในแคตตาล็อก หรือชื่อใดก็ได้",
    "Model or characteristic": "รุ่นหรือคุณลักษณะ",
    "left, right, both": "left, right, both — ซ้าย ขวา ทั้งสองข้าง",
    "front, rear, both": "front, rear, both — หน้า หลัง ทั้งสอง",
    "upper, lower, both": "upper, lower, both — บน ล่าง ทั้งสอง",
    "inner, outer, both": "inner, outer, both — ใน นอก ทั้งสอง",
    "original or analog": "original (ของแท้) หรือ analog (ทดแทน)",
    "new or used": "new (ใหม่) หรือ used (ใช้แล้ว)",
    "Barcode of the item": "บาร์โค้ดของรายการ",
    "Link to a photo": "ลิงก์ไปยังรูปภาพ",
    "Your own ID of the item, used to find it again": ("ID ของรายการในระบบของคุณ ใช้เพื่อค้นหาอีกครั้ง"),
    # Initial stock
    "ID of an existing item (the «items» sheet); required": (
        "ID ของรายการที่มีอยู่ (แผ่นงาน «items»); จำเป็น"
    ),
    "ID of a warehouse context (the «contexts» sheet); required": (
        "ID ของบริบทคลังสินค้า (แผ่นงาน «contexts»); จำเป็น"
    ),
    "Material: code of a new lot in this file; rows with one code make one lot": (
        "วัสดุ: รหัสล็อตใหม่ในไฟล์นี้; แถวที่มีรหัสเดียวกันรวมเป็นล็อตเดียว"
    ),
    "Material: LOT ID of an existing lot instead of a code of a new one": (
        "วัสดุ: LOT ID ของล็อตที่มีอยู่ แทนรหัสของล็อตใหม่"
    ),
    "External lot number of the producer": "หมายเลขล็อตภายนอกของผู้ผลิต",
    "Production date, YYYY-MM-DD": "วันที่ผลิต YYYY-MM-DD",
    "Expiry date, YYYY-MM-DD": "วันหมดอายุ YYYY-MM-DD",
    "Content of the packaging in the base unit; empty — loose material": (
        "ปริมาณในบรรจุภัณฑ์ในหน่วยฐาน; ว่าง — วัสดุไม่มีบรรจุภัณฑ์"
    ),
    "Name of a new packaging, used only when it is created": ("ชื่อบรรจุภัณฑ์ใหม่ ใช้เฉพาะเมื่อสร้างบรรจุภัณฑ์"),
    "Number of closed packages": "จำนวนบรรจุภัณฑ์ที่ยังไม่เปิด",
    "yes — one opened package; its actual rest goes to «quantity»": (
        "yes — บรรจุภัณฑ์ที่เปิดแล้วหนึ่งชิ้น; ปริมาณคงเหลือจริงใส่ใน «quantity»"
    ),
    "Base unit: loose material or the rest of an opened package; for an individual item empty "
    "or 1": ("หน่วยฐาน: วัสดุไม่มีบรรจุภัณฑ์หรือปริมาณคงเหลือของบรรจุภัณฑ์ที่เปิดแล้ว; สำหรับสินค้ารายชิ้นให้ว่างหรือ 1"),
    "Purchase cost per base unit in the company currency": "ต้นทุนซื้อต่อหน่วยฐานในสกุลเงินของบริษัท",
    "Selling price: of the unit, or of the lot in the lot price mode": (
        "ราคาขาย: ต่อหน่วย หรือต่อล็อตในโหมดราคาตามล็อต"
    ),
    "Individual item: external or serial ID of the unit": (
        "สินค้ารายชิ้น: ID ภายนอกหรือหมายเลขซีเรียลของชิ้น"
    ),
    "Marks of the place, separated by commas": "ป้ายตำแหน่งจัดเก็บ คั่นด้วยจุลภาค",
}

EXAMPLE_NOTE = "แถวที่สองของแผ่นงาน «{sheet}» เป็นตัวอย่าง: แทนที่หรือลบออก"


def thai(english: str) -> str:
    return TH.get(english, "")
