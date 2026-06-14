# swedeniran

پنل ساده برای پیدا کردن افراد دارای شرکت در سوئد بر اساس اسم — داده از [allabolag.se](https://www.allabolag.se).

## اجرا

```bash
cd ~/Documents/GitHub/swedeniran
python3 -m pip install -r requirements.txt
python3 -m uvicorn main:app --reload --port 8787
```

مرورگر: **http://127.0.0.1:8787**

اولین اجرا خودکار **Homayoun** را در صف می‌گذارد و worker در پس‌زمینه لیست افراد و جزئیات شرکت‌ها را می‌گیرد.

## استفاده

- سمت چپ: لیست اسم‌های جستجو شده + وضعیت پیشرفت
- وسط: جدول افراد (به‌صورت زنده پر می‌شود)
- راست: کلیک روی شخص → شرکت‌ها، درآمد، تماس
- فیلد «افزودن»: اسم جدید → در صف background

## env

- `SWEDENIRAN_SEED=Homayoun` — اسم seed اول (پیش‌فرض Homayoun)

## نکته

allabolag برای **شخص** ایمیل/تلفن شخصی نمی‌دهد؛ تماس از صفحه **شرکت** است.
