from __future__ import annotations

import math
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Iterator
from urllib.parse import quote_plus

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "akakce_catalog.db"
INDEX_FILE = ROOT / "index.html"

CATEGORY_DATA = [
    ("monitor", "Monitör", "🖥️"),
    ("ekran-karti", "Ekran Kartı", "🎮"),
    ("laptop", "Laptop / Dizüstü", "💻"),
    ("cep-telefonu", "Cep Telefonu", "📱"),
    ("islemci", "İşlemci", "⚙️"),
    ("ram", "RAM", "🧠"),
    ("ssd-sabit-disk", "SSD / Sabit Disk", "💾"),
    ("anakart", "Anakart", "🔌"),
    ("oyuncu-kulakligi", "Oyuncu Kulaklığı", "🎧"),
    ("klavye", "Klavye", "⌨️"),
    ("mouse", "Mouse", "🖱️"),
    ("akilli-saat", "Akıllı Saat", "⌚"),
    ("tablet", "Tablet", "📲"),
    ("televizyon", "Televizyon", "📺"),
    ("oyun-konsolu", "Oyun Konsolu", "🕹️"),
    ("kulaklik", "Kulaklık", "🎵"),
    ("depolama", "Harici Depolama", "🗄️"),
    ("bilgisayar", "Masaüstü Bilgisayar", "🖥️"),
]

# Curated demo catalog; prices and offers are illustrative, not live market data.
PRODUCT_DATA = [
    ("monitor", "ASUS TUF Gaming VG27AQ1A 27 inç QHD 170Hz", "ASUS", 10999, "27 inç, 2560×1440, Fast IPS, 170 Hz, 1 ms, Adaptive-Sync"),
    ("monitor", "Samsung Odyssey G5 LS27CG552 27 inç QHD 165Hz", "Samsung", 8299, "27 inç, QHD, VA panel, 165 Hz, 1 ms, HDR10"),
    ("monitor", "MSI MAG 274QRF QD E2 27 inç QHD 180Hz", "MSI", 14999, "Rapid IPS, QHD, 180 Hz, Quantum Dot, USB-C"),
    ("monitor", "AOC 24G4X 23.8 inç Full HD 180Hz", "AOC", 5499, "Fast IPS, 1920×1080, 180 Hz, 1 ms, Adaptive-Sync"),
    ("monitor", "Dell UltraSharp U2723QE 27 inç 4K", "Dell", 22999, "27 inç IPS Black, 4K UHD, USB-C hub, 90 W güç aktarımı"),
    ("monitor", "LG UltraGear 27GS60F-B 27 inç 180Hz", "LG", 6999, "Full HD IPS, 180 Hz, 1 ms, HDR10, FreeSync"),
    ("ekran-karti", "ASUS Dual GeForce RTX 4060 OC 8GB", "ASUS", 15999, "NVIDIA Ada Lovelace, 8 GB GDDR6, DLSS 3, çift fan"),
    ("ekran-karti", "MSI GeForce RTX 4070 SUPER Ventus 2X 12GB", "MSI", 32999, "12 GB GDDR6X, DLSS 3.5, ışın izleme, çift fan"),
    ("ekran-karti", "Gigabyte GeForce RTX 4070 Ti SUPER Gaming OC 16GB", "Gigabyte", 44999, "16 GB GDDR6X, WINDFORCE soğutma, DLSS 3"),
    ("ekran-karti", "Sapphire Pulse Radeon RX 7800 XT 16GB", "AMD", 23999, "16 GB GDDR6, RDNA 3, 1440p oyun, çift fan"),
    ("ekran-karti", "Gigabyte Radeon RX 7600 Gaming OC 8GB", "Gigabyte", 11999, "8 GB GDDR6, RDNA 3, üç fanlı soğutma"),
    ("ekran-karti", "ASUS TUF Gaming GeForce RTX 4080 SUPER 16GB", "ASUS", 59999, "16 GB GDDR6X, DLSS 3, gelişmiş ışın izleme"),
    ("laptop", "Lenovo LOQ 15IRX9 i7-13650HX 16GB 512GB RTX 4060", "Lenovo", 42999, "Core i7-13650HX, 15.6 inç FHD 144 Hz, 16 GB RAM, RTX 4060"),
    ("laptop", "ASUS TUF Gaming A15 FA507NV Ryzen 7 7735HS", "ASUS", 37999, "Ryzen 7, 15.6 inç 144 Hz, 16 GB RAM, RTX 4060"),
    ("laptop", "Apple MacBook Air 13 M3 16GB 512GB", "Apple", 49999, "Apple M3, 13.6 inç Liquid Retina, 16 GB birleşik bellek"),
    ("laptop", "Dell Inspiron 15 3530 Core i5 16GB 512GB", "Dell", 23999, "Core i5-1335U, 15.6 inç FHD, 16 GB DDR4, 512 GB SSD"),
    ("laptop", "HP Victus 16 Ryzen 7 8845HS RTX 4070", "HP", 55999, "Ryzen 7 8845HS, 16.1 inç 144 Hz, RTX 4070, 1 TB SSD"),
    ("laptop", "Lenovo IdeaPad Slim 3 Ryzen 5 7530U 16GB", "Lenovo", 18999, "Ryzen 5 7530U, 15.6 inç FHD IPS, 16 GB RAM, 512 GB SSD"),
    ("cep-telefonu", "Apple iPhone 15 128GB", "Apple", 41999, "6.1 inç Super Retina XDR, A16 Bionic, 48 MP ana kamera"),
    ("cep-telefonu", "Apple iPhone 16 Pro 256GB", "Apple", 74999, "6.3 inç ProMotion OLED, A18 Pro, 48 MP üçlü kamera"),
    ("cep-telefonu", "Samsung Galaxy S24 Ultra 256GB", "Samsung", 61999, "6.8 inç Dynamic AMOLED 2X, Snapdragon 8 Gen 3, S Pen"),
    ("cep-telefonu", "Samsung Galaxy A55 5G 256GB", "Samsung", 17999, "6.6 inç Super AMOLED, 120 Hz, 50 MP OIS, 5000 mAh"),
    ("cep-telefonu", "Xiaomi Redmi Note 13 Pro 5G 256GB", "Xiaomi", 13999, "6.67 inç AMOLED 120 Hz, 200 MP OIS, 5100 mAh"),
    ("cep-telefonu", "Samsung Galaxy Z Flip6 256GB", "Samsung", 49999, "Katlanabilir Dynamic AMOLED 2X, Snapdragon 8 Gen 3, 50 MP"),
    ("islemci", "AMD Ryzen 7 7800X3D 4.2GHz AM5", "AMD", 15999, "8 çekirdek, 16 izlek, 96 MB önbellek, 120 W TDP"),
    ("islemci", "AMD Ryzen 5 7600 3.8GHz AM5", "AMD", 7999, "6 çekirdek, 12 izlek, 32 MB önbellek, 65 W TDP"),
    ("islemci", "Intel Core i5-14600KF 3.5GHz LGA1700", "Intel", 10999, "14 çekirdek, 20 izlek, 5.3 GHz turbo, kilitsiz"),
    ("islemci", "Intel Core i7-14700K 3.4GHz LGA1700", "Intel", 17999, "20 çekirdek, 28 izlek, 5.6 GHz turbo, UHD 770"),
    ("islemci", "AMD Ryzen 9 9950X 4.3GHz AM5", "AMD", 30999, "16 çekirdek, 32 izlek, Zen 5, 64 MB L3 önbellek"),
    ("islemci", "Intel Core Ultra 7 265K LGA1851", "Intel", 20999, "20 çekirdek, 20 izlek, NPU, 5.5 GHz turbo"),
    ("ram", "Corsair Vengeance 32GB (2x16) DDR5 6000MHz", "Corsair", 3999, "32 GB kit, DDR5-6000, CL36, AMD EXPO ve Intel XMP"),
    ("ram", "Kingston Fury Beast 16GB DDR5 5600MHz", "Kingston", 1799, "16 GB DDR5, 5600 MT/s, CL40, Intel XMP 3.0"),
    ("ram", "G.Skill Trident Z5 Neo RGB 32GB DDR5 6000MHz", "G.Skill", 4999, "2×16 GB DDR5, 6000 MT/s, CL30, AMD EXPO"),
    ("ram", "Crucial Pro 64GB (2x32) DDR5 5600MHz", "Crucial", 6999, "64 GB kit, DDR5-5600, düşük profilli soğutucu"),
    ("ssd-sabit-disk", "Samsung 990 PRO 2TB NVMe M.2 SSD", "Samsung", 6999, "PCIe 4.0 x4, 7450/6900 MB/sn, 2 GB DRAM"),
    ("ssd-sabit-disk", "WD Black SN770 1TB NVMe M.2 SSD", "Western Digital", 3299, "PCIe 4.0, 5150 MB/sn okuma, M.2 2280"),
    ("ssd-sabit-disk", "Kingston NV2 1TB NVMe M.2 SSD", "Kingston", 2299, "PCIe 4.0 x4, 3500 MB/sn okuma, M.2 2280"),
    ("ssd-sabit-disk", "Crucial MX500 1TB SATA SSD", "Crucial", 2799, "2.5 inç SATA, 560/510 MB/sn, 3D NAND"),
    ("ssd-sabit-disk", "Seagate BarraCuda 2TB 3.5 inç HDD", "Seagate", 2599, "7200 RPM, SATA 6 Gb/sn, 256 MB önbellek"),
    ("anakart", "MSI MAG B650 Tomahawk WiFi AM5", "MSI", 9999, "AMD B650, DDR5, Wi-Fi 6E, 2.5G LAN, PCIe 4.0"),
    ("anakart", "ASUS TUF Gaming B650-PLUS WiFi AM5", "ASUS", 10499, "AMD B650, DDR5, Wi-Fi 6, 3× M.2, USB-C"),
    ("anakart", "Gigabyte B760 AORUS Elite AX DDR5", "Gigabyte", 9299, "Intel LGA1700, DDR5, Wi-Fi 6E, PCIe 5.0"),
    ("anakart", "ASUS ROG Strix Z790-F Gaming WiFi II", "ASUS", 20999, "Intel Z790, LGA1700, DDR5, Wi-Fi 7, PCIe 5.0"),
    ("oyuncu-kulakligi", "SteelSeries Arctis Nova 7 Wireless", "SteelSeries", 9999, "2.4 GHz kablosuz, 38 saat pil, çift ses bağlantısı"),
    ("oyuncu-kulakligi", "Logitech G Pro X 2 Lightspeed", "Logitech", 10999, "50 mm grafen sürücüler, Lightspeed, 50 saat pil"),
    ("oyuncu-kulakligi", "HyperX Cloud III Wireless", "HyperX", 5999, "2.4 GHz kablosuz, 120 saat pil, 53 mm sürücüler"),
    ("oyuncu-kulakligi", "Razer BlackShark V2 X", "Razer", 2499, "3.5 mm bağlantı, 50 mm sürücüler, 240 g hafif tasarım"),
    ("klavye", "Logitech G Pro X TKL Lightspeed", "Logitech", 7999, "Kablosuz mekanik TKL, GX anahtarlar, RGB LIGHTSYNC"),
    ("klavye", "SteelSeries Apex Pro TKL Gen 3", "SteelSeries", 11999, "OmniPoint 3.0 ayarlanabilir anahtar, OLED ekran, RGB"),
    ("klavye", "Keychron K2 Pro RGB Hot-swap", "Keychron", 4499, "75% kablosuz mekanik, QMK/VIA, hot-swap anahtar"),
    ("klavye", "Redragon K552 Kumara RGB", "Redragon", 1499, "TKL mekanik, RGB aydınlatma, metal üst plaka"),
    ("mouse", "Logitech G Pro X Superlight 2", "Logitech", 5499, "60 g, HERO 2 sensör, 32.000 DPI, Lightspeed"),
    ("mouse", "Razer DeathAdder V3 Pro", "Razer", 4999, "Ergonomik kablosuz, Focus Pro 30K sensör, 63 g"),
    ("mouse", "Logitech G305 Lightspeed", "Logitech", 1499, "Kablosuz HERO sensör, 12.000 DPI, 250 saat pil"),
    ("mouse", "SteelSeries Rival 5", "SteelSeries", 2299, "18.000 CPI optik sensör, 9 programlanabilir tuş"),
    ("akilli-saat", "Apple Watch Series 10 GPS 46mm", "Apple", 17999, "Always-On Retina, EKG, uyku apnesi bildirimi, 50 m suya dayanıklı"),
    ("akilli-saat", "Samsung Galaxy Watch7 44mm Bluetooth", "Samsung", 9999, "Super AMOLED, BioActive sensör, Wear OS, GPS"),
    ("akilli-saat", "Huawei Watch GT 5 46mm", "Huawei", 8999, "AMOLED ekran, 14 güne kadar pil, çift bant GPS"),
    ("akilli-saat", "Garmin Forerunner 265", "Garmin", 17999, "AMOLED, çok bantlı GPS, antrenman hazırlık puanı"),
    ("tablet", "Apple iPad Air 11 inç M2 Wi-Fi 128GB", "Apple", 27999, "M2 çip, Liquid Retina, Apple Pencil Pro desteği"),
    ("tablet", "Samsung Galaxy Tab S9 FE Wi-Fi 128GB", "Samsung", 14999, "10.9 inç LCD, S Pen, IP68, Exynos 1380"),
    ("tablet", "Lenovo Tab P12 8GB 256GB", "Lenovo", 12999, "12.7 inç 3K ekran, JBL dört hoparlör, kalem desteği"),
    ("tablet", "Xiaomi Pad 6 8GB 256GB", "Xiaomi", 11999, "11 inç 2.8K 144 Hz, Snapdragon 870, Dolby Vision"),
    ("televizyon", "Samsung 55 inç QN90D Neo QLED 4K", "Samsung", 59999, "Neo QLED Mini LED, 4K 144 Hz, Quantum Matrix"),
    ("televizyon", "LG OLED55C4 55 inç OLED evo 4K", "LG", 64999, "OLED evo, 4K 144 Hz, α9 Gen7 işlemci, Dolby Vision"),
    ("televizyon", "TCL 55C755 55 inç Mini LED 4K", "TCL", 32999, "QD-Mini LED, 4K 144 Hz, Google TV, Dolby Vision IQ"),
    ("televizyon", "Samsung 50 inç DU8000 Crystal UHD 4K", "Samsung", 22999, "Crystal UHD, 4K, Tizen Smart TV, HDR10+"),
    ("oyun-konsolu", "Sony PlayStation 5 Slim Digital Edition", "Sony", 23999, "1 TB SSD, 4K oyun, ışın izleme, DualSense kontrolcü"),
    ("oyun-konsolu", "Sony PlayStation 5 Slim Disc Edition", "Sony", 26999, "1 TB SSD, Ultra HD Blu-ray, 4K oyun, DualSense"),
    ("oyun-konsolu", "Microsoft Xbox Series X 1TB", "Microsoft", 27999, "4K oyun, 1 TB NVMe SSD, Quick Resume, Game Pass"),
    ("oyun-konsolu", "Nintendo Switch OLED 64GB", "Nintendo", 14999, "7 inç OLED, çıkarılabilir Joy-Con, TV ve taşınabilir mod"),
    ("kulaklik", "Apple AirPods Pro 2 USB-C", "Apple", 9999, "Aktif gürültü engelleme, uyarlanabilir ses, USB-C şarj kutusu"),
    ("kulaklik", "Sony WH-1000XM5 Kablosuz Kulaklık", "Sony", 11999, "Aktif gürültü engelleme, 30 saat pil, LDAC"),
    ("kulaklik", "Samsung Galaxy Buds3 Pro", "Samsung", 7499, "Adaptif ANC, 24-bit ses, IP57, Galaxy AI"),
    ("kulaklik", "JBL Tune 770NC Kablosuz Kulaklık", "JBL", 3299, "Uyarlanabilir ANC, 70 saate kadar pil, Bluetooth 5.3"),
    ("depolama", "SanDisk Extreme Portable SSD 1TB", "SanDisk", 4999, "USB 3.2 Gen 2, 1050 MB/sn, IP65, taşınabilir SSD"),
    ("depolama", "Samsung T7 Shield 2TB Taşınabilir SSD", "Samsung", 8999, "USB 3.2, 1050 MB/sn, IP65 dayanıklılık"),
    ("depolama", "WD My Passport 4TB Taşınabilir HDD", "Western Digital", 4999, "USB 3.2, 4 TB kapasite, parola koruması"),
    ("bilgisayar", "MSI MAG Infinite S3 i5-14400F RTX 4060", "MSI", 42999, "Core i5-14400F, RTX 4060, 16 GB DDR5, 1 TB NVMe SSD"),
    ("bilgisayar", "HP Omen 25L Ryzen 7 8700G RTX 4060 Ti", "HP", 58999, "Ryzen 7 8700G, RTX 4060 Ti, 32 GB DDR5, 1 TB SSD"),
    ("bilgisayar", "Lenovo Legion Tower 5 i7-14700F RTX 4070", "Lenovo", 74999, "Core i7-14700F, RTX 4070, 32 GB DDR5, 1 TB SSD"),
    ("monitor", "LG UltraGear 32GS95UE 32 inç 4K OLED 240Hz", "LG", 52999, "32 inç OLED, 4K 240 Hz veya Full HD 480 Hz, 0.03 ms"),
    ("monitor", "Gigabyte M27Q X 27 inç QHD 240Hz", "Gigabyte", 18999, "27 inç SuperSpeed IPS, QHD, 240 Hz, KVM"),
    ("ekran-karti", "MSI GeForce RTX 4060 Ti Ventus 2X Black 8GB", "MSI", 19999, "8 GB GDDR6, Ada Lovelace, DLSS 3, çift fan"),
    ("ekran-karti", "Sapphire Pulse Radeon RX 7900 GRE 16GB", "AMD", 28999, "16 GB GDDR6, RDNA 3, 1440p ve 4K oyun"),
    ("ekran-karti", "ASUS Dual GeForce RTX 4070 EVO OC 12GB", "ASUS", 27999, "12 GB GDDR6X, DLSS 3, ışın izleme, çift fan"),
    ("laptop", "Apple MacBook Pro 14 M3 Pro 18GB 512GB", "Apple", 79999, "M3 Pro, 14.2 inç Liquid Retina XDR, 18 GB birleşik bellek"),
    ("laptop", "ASUS Zenbook 14 OLED UX3405 Core Ultra 7", "ASUS", 44999, "Core Ultra 7, 14 inç 3K OLED, 32 GB RAM, 1 TB SSD"),
    ("laptop", "Lenovo Yoga Slim 7 14IMH9 Core Ultra 5", "Lenovo", 37999, "Core Ultra 5, 14 inç OLED, 16 GB RAM, 1 TB SSD"),
    ("laptop", "HP Pavilion Plus 14 Ryzen 7 7840U", "HP", 32999, "Ryzen 7 7840U, 14 inç 2.8K OLED, 16 GB RAM"),
    ("cep-telefonu", "Apple iPhone 15 Pro 256GB", "Apple", 59999, "6.1 inç Super Retina XDR, A17 Pro, titanyum kasa"),
    ("cep-telefonu", "Samsung Galaxy S24 256GB", "Samsung", 35999, "6.2 inç Dynamic AMOLED 2X, Exynos 2400, 50 MP kamera"),
    ("cep-telefonu", "Xiaomi 14T Pro 512GB", "Xiaomi", 32999, "6.67 inç AMOLED 144 Hz, Dimensity 9300+, Leica kamera"),
    ("cep-telefonu", "Google Pixel 9 Pro 256GB", "Google", 54999, "6.3 inç LTPO OLED, Tensor G4, 50 MP üçlü kamera"),
    ("islemci", "AMD Ryzen 5 9600X 3.9GHz AM5", "AMD", 10999, "6 çekirdek, 12 izlek, Zen 5, 5.4 GHz boost"),
    ("islemci", "AMD Ryzen 7 9700X 3.8GHz AM5", "AMD", 15999, "8 çekirdek, 16 izlek, Zen 5, 65 W TDP"),
    ("islemci", "Intel Core i5-14400F 2.5GHz LGA1700", "Intel", 7499, "10 çekirdek, 16 izlek, 4.7 GHz turbo, soğutucu dahil"),
    ("islemci", "Intel Core i9-14900K 3.2GHz LGA1700", "Intel", 24999, "24 çekirdek, 32 izlek, 6.0 GHz turbo, UHD 770"),
    ("ram", "Corsair Vengeance 64GB (2x32) DDR5 6000MHz", "Corsair", 7999, "64 GB kit, DDR5-6000, AMD EXPO ve Intel XMP"),
    ("ssd-sabit-disk", "WD Black SN850X 2TB NVMe M.2 SSD", "Western Digital", 6999, "PCIe 4.0, 7300 MB/sn okuma, oyun modu"),
    ("anakart", "ASUS ROG Strix B650E-F Gaming WiFi AM5", "ASUS", 13999, "AMD B650E, DDR5, Wi-Fi 6E, PCIe 5.0"),
    ("oyuncu-kulakligi", "Logitech G733 Lightspeed RGB Kablosuz", "Logitech", 4999, "Lightspeed kablosuz, Blue VO!CE mikrofon, 29 saat pil"),
    ("klavye", "Logitech G915 X Lightspeed TKL", "Logitech", 8999, "Düşük profilli kablosuz mekanik, Lightspeed, RGB"),
    ("mouse", "Logitech MX Master 3S Kablosuz Mouse", "Logitech", 3999, "8K DPI sensör, sessiz tıklama, USB-C hızlı şarj"),
    ("akilli-saat", "Apple Watch SE 2 GPS 44mm", "Apple", 10999, "Retina OLED, kalp ritmi bildirimi, 50 m suya dayanıklılık"),
    ("tablet", "Apple iPad 10. Nesil Wi-Fi 256GB", "Apple", 21999, "10.9 inç Liquid Retina, A14 Bionic, USB-C"),
    ("tablet", "Samsung Galaxy Tab S9 Ultra Wi-Fi 256GB", "Samsung", 44999, "14.6 inç Dynamic AMOLED 2X, Snapdragon 8 Gen 2, S Pen"),
    ("kulaklik", "Sony WF-1000XM5 Kablosuz Kulak İçi", "Sony", 8999, "Aktif gürültü engelleme, LDAC, 24 saate kadar pil"),
    ("televizyon", "LG OLED65C4 65 inç OLED evo 4K", "LG", 89999, "65 inç OLED evo, 4K 144 Hz, α9 Gen7, Dolby Vision"),
    ("oyun-konsolu", "Sony PlayStation 5 Pro 2TB", "Sony", 39999, "2 TB SSD, gelişmiş ışın izleme, 4K oyun, Wi-Fi 7"),
]

STORES = [
    ("Amazon Türkiye", "https://www.amazon.com.tr/s?k={query}"),
    ("Hepsiburada", "https://www.hepsiburada.com/ara?q={query}"),
    ("Trendyol", "https://www.trendyol.com/sr?q={query}"),
    ("İncehesap", "https://www.incehesap.com/arama/?q={query}"),
]

IMAGE_URLS = {
    "monitor": "https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?auto=format&fit=crop&w=800&q=85",
    "ekran-karti": "https://images.unsplash.com/photo-1593305841991-05c297ba4575?auto=format&fit=crop&w=800&q=85",
    "laptop": "https://images.unsplash.com/photo-1593642632823-8f785ba67e45?auto=format&fit=crop&w=800&q=85",
    "cep-telefonu": "https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?auto=format&fit=crop&w=800&q=85",
    "islemci": "https://images.unsplash.com/photo-1591799264318-7e6ef8ddb7ea?auto=format&fit=crop&w=800&q=85",
    "ram": "https://images.unsplash.com/photo-1591799264318-7e6ef8ddb7ea?auto=format&fit=crop&w=800&q=85",
    "ssd-sabit-disk": "https://images.unsplash.com/photo-1597872200969-2b65d56bd16b?auto=format&fit=crop&w=800&q=85",
    "anakart": "https://images.unsplash.com/photo-1591799264318-7e6ef8ddb7ea?auto=format&fit=crop&w=800&q=85",
    "oyuncu-kulakligi": "https://images.unsplash.com/photo-1583394838336-acd977736f90?auto=format&fit=crop&w=800&q=85",
    "klavye": "https://images.unsplash.com/photo-1587829741301-dc798b83add3?auto=format&fit=crop&w=800&q=85",
    "mouse": "https://images.unsplash.com/photo-1527814050087-3793815479db?auto=format&fit=crop&w=800&q=85",
    "akilli-saat": "https://images.unsplash.com/photo-1546868871-7041f2a55e12?auto=format&fit=crop&w=800&q=85",
    "tablet": "https://images.unsplash.com/photo-1544244015-0df4b3ffc6b0?auto=format&fit=crop&w=800&q=85",
    "televizyon": "https://images.unsplash.com/photo-1593359677879-a4bb92f829d1?auto=format&fit=crop&w=800&q=85",
    "oyun-konsolu": "https://images.unsplash.com/photo-1593305841991-05c297ba4575?auto=format&fit=crop&w=800&q=85",
    "kulaklik": "https://images.unsplash.com/photo-1505751172876-fa1923c5c528?auto=format&fit=crop&w=800&q=85",
    "depolama": "https://images.unsplash.com/photo-1597872200969-2b65d56bd16b?auto=format&fit=crop&w=800&q=85",
    "bilgisayar": "https://images.unsplash.com/photo-1593305841991-05c297ba4575?auto=format&fit=crop&w=800&q=85",
}

@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(DATABASE, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                icon TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                category TEXT NOT NULL REFERENCES categories(id),
                brand TEXT NOT NULL,
                current_lowest_price REAL NOT NULL,
                old_price REAL NOT NULL,
                lowest_store_name TEXT NOT NULL,
                image_url TEXT NOT NULL,
                description TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS store_prices (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                store_name TEXT NOT NULL,
                price REAL NOT NULL,
                product_url TEXT NOT NULL,
                UNIQUE(product_id, store_name)
            );
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                recorded_on TEXT NOT NULL,
                price REAL NOT NULL,
                UNIQUE(product_id, recorded_on)
            );
            CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
            CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand);
            CREATE INDEX IF NOT EXISTS idx_products_price ON products(current_lowest_price);
            CREATE INDEX IF NOT EXISTS idx_store_prices_store ON store_prices(store_name);
            CREATE INDEX IF NOT EXISTS idx_price_history_product_date
                ON price_history(product_id, recorded_on);
            """
        )
        db.executemany(
            "INSERT OR IGNORE INTO categories (id, name, icon) VALUES (?, ?, ?)",
            CATEGORY_DATA,
        )
        store_names = tuple(store_name for store_name, _ in STORES)
        db.execute(
            f"DELETE FROM store_prices WHERE store_name NOT IN ({','.join('?' for _ in store_names)})",
            store_names,
        )
        today = date.today()
        for product_id, (category, title, brand, base_price, description) in enumerate(
            PRODUCT_DATA, start=1
        ):
            low_store_index = product_id % len(STORES)
            low_price = round(base_price * (0.94 + (product_id % 8) * 0.008), 2)
            old_price = round(low_price * (1.08 + (product_id % 5) * 0.012), 2)
            image_url = IMAGE_URLS[category]
            db.execute(
                """INSERT INTO products
                   (id, title, category, brand, current_lowest_price, old_price,
                    lowest_store_name, image_url, description)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     title = excluded.title,
                     category = excluded.category,
                     brand = excluded.brand,
                     current_lowest_price = excluded.current_lowest_price,
                     old_price = excluded.old_price,
                     lowest_store_name = excluded.lowest_store_name,
                     image_url = excluded.image_url,
                     description = excluded.description""",
                (
                    product_id,
                    title,
                    category,
                    brand,
                    low_price,
                    old_price,
                    STORES[low_store_index][0],
                    image_url,
                    description,
                ),
            )

            offer_rows = []
            for store_index, (store_name, url_template) in enumerate(STORES):
                multiplier = 1 + ((store_index - low_store_index) % len(STORES)) * 0.018
                offer_price = round(low_price * multiplier, 2)
                offer_rows.append(
                    (
                        product_id,
                        store_name,
                        offer_price,
                        url_template.format(query=quote_plus(title)),
                    )
                )
            db.executemany(
                """INSERT INTO store_prices
                   (product_id, store_name, price, product_url) VALUES (?, ?, ?, ?)
                   ON CONFLICT(product_id, store_name) DO UPDATE SET
                     price = excluded.price,
                     product_url = excluded.product_url""",
                offer_rows,
            )

            history_rows = []
            for days_ago in range(365, -1, -1):
                recorded_on = today - timedelta(days=days_ago)
                wave = max(0, math.sin((365 - days_ago + product_id * 11) / 28)) * 0.025
                markup = 0 if days_ago == 0 else days_ago * 0.00025 + wave
                price = round(low_price * (1 + markup), 2)
                history_rows.append((product_id, recorded_on.isoformat(), price))
            db.executemany(
                """INSERT OR IGNORE INTO price_history (product_id, recorded_on, price)
                   VALUES (?, ?, ?)""",
                history_rows,
            )


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="İndirim Radarı", version="1.0.0", lifespan=lifespan)


def product_card(row: sqlite3.Row) -> dict:
    return dict(row)


@app.get("/api/categories")
def get_categories() -> list[dict]:
    with connect() as db:
        rows = db.execute(
            """SELECT c.id, c.name, c.icon, COUNT(p.id) AS product_count
               FROM categories c LEFT JOIN products p ON p.category = c.id
               GROUP BY c.id ORDER BY c.name COLLATE NOCASE"""
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/brands")
def get_brands(category: str = "") -> list[str]:
    with connect() as db:
        if category:
            rows = db.execute(
                """SELECT DISTINCT brand FROM products WHERE category = ?
                   ORDER BY brand COLLATE NOCASE""",
                (category,),
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT DISTINCT brand FROM products ORDER BY brand COLLATE NOCASE"
            ).fetchall()
    return [row["brand"] for row in rows]


@app.get("/api/home")
def get_home() -> dict:
    with connect() as db:
        popular = db.execute(
            """SELECT p.*, c.name AS category_name,
                      (SELECT COUNT(*) FROM store_prices s WHERE s.product_id = p.id) AS offer_count,
                      (SELECT s.product_url FROM store_prices s
                       WHERE s.product_id = p.id ORDER BY s.price ASC LIMIT 1) AS lowest_store_url
               FROM products p JOIN categories c ON c.id = p.category
               ORDER BY p.current_lowest_price ASC, p.id ASC LIMIT 12"""
        ).fetchall()
        deals = db.execute(
            """SELECT p.*, c.name AS category_name,
                      (SELECT COUNT(*) FROM store_prices s WHERE s.product_id = p.id) AS offer_count,
                      (SELECT s.product_url FROM store_prices s
                       WHERE s.product_id = p.id ORDER BY s.price ASC LIMIT 1) AS lowest_store_url
               FROM products p JOIN categories c ON c.id = p.category
               ORDER BY (p.old_price - p.current_lowest_price) / p.old_price DESC, p.id ASC
               LIMIT 8"""
        ).fetchall()
        categories = db.execute(
            """SELECT c.id, c.name, c.icon, COUNT(p.id) AS product_count
               FROM categories c LEFT JOIN products p ON p.category = c.id
               GROUP BY c.id ORDER BY c.name COLLATE NOCASE"""
        ).fetchall()
        return {
            "popular": [product_card(row) for row in popular],
            "deals": [product_card(row) for row in deals],
            "categories": [dict(row) for row in categories],
        }


@app.get("/api/products")
def get_products(
    q: str = "",
    category: str = "",
    brand: str = "",
    store: str = "",
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    sort: str = "relevance",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> dict:
    clauses: list[str] = []
    values: list[object] = []
    if q.strip():
        clauses.append("(p.title LIKE ? OR p.brand LIKE ? OR p.description LIKE ?)")
        search = f"%{q.strip()[:100]}%"
        values.extend((search, search, search))
    if category:
        clauses.append("p.category = ?")
        values.append(category)
    if brand:
        clauses.append("p.brand = ?")
        values.append(brand)
    if min_price is not None:
        clauses.append("p.current_lowest_price >= ?")
        values.append(min_price)
    if max_price is not None:
        clauses.append("p.current_lowest_price <= ?")
        values.append(max_price)
    if store:
        clauses.append(
            "EXISTS (SELECT 1 FROM store_prices s WHERE s.product_id = p.id AND s.store_name = ?)"
        )
        values.append(store)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_by = {
        "price_asc": "p.current_lowest_price ASC, p.id ASC",
        "price_desc": "p.current_lowest_price DESC, p.id ASC",
        "discount": "(p.old_price - p.current_lowest_price) / p.old_price DESC, p.id ASC",
        "relevance": "p.title COLLATE NOCASE ASC, p.id ASC",
    }.get(sort, "p.title COLLATE NOCASE ASC, p.id ASC")
    with connect() as db:
        rows = db.execute(
            f"""SELECT p.*, c.name AS category_name,
                       (SELECT COUNT(*) FROM store_prices s WHERE s.product_id = p.id) AS offer_count,
                       (SELECT s.product_url FROM store_prices s
                        WHERE s.product_id = p.id ORDER BY s.price ASC LIMIT 1) AS lowest_store_url
                FROM products p JOIN categories c ON c.id = p.category
                {where} ORDER BY {order_by} LIMIT ?""",
            (*values, limit),
        ).fetchall()
        total = db.execute(
            f"SELECT COUNT(*) FROM products p {where}", values
        ).fetchone()[0]
        products = [product_card(row) for row in rows]
    return {"items": products, "total": total}


@app.get("/api/products/{product_id}")
def get_product(product_id: int) -> dict:
    with connect() as db:
        row = db.execute(
            """SELECT p.*, c.name AS category_name,
                      (SELECT COUNT(*) FROM store_prices s WHERE s.product_id = p.id) AS offer_count,
                      (SELECT s.product_url FROM store_prices s
                       WHERE s.product_id = p.id ORDER BY s.price ASC LIMIT 1) AS lowest_store_url
               FROM products p JOIN categories c ON c.id = p.category WHERE p.id = ?""",
            (product_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        product = product_card(row)
        offers = db.execute(
            """SELECT store_name, price, product_url FROM store_prices
               WHERE product_id = ? ORDER BY price ASC, store_name ASC""",
            (product_id,),
        ).fetchall()
        product["offers"] = [dict(offer) for offer in offers]
    return product


@app.get("/api/products/{product_id}/history")
def get_price_history(
    product_id: int,
    period: str = "1y",
) -> dict:
    period_days = {"1w": 7, "1m": 30, "6m": 183, "1y": 365}
    if period not in period_days:
        raise HTTPException(status_code=422, detail="Geçersiz fiyat geçmişi dönemi")
    start = date.today() - timedelta(days=period_days[period])
    with connect() as db:
        exists = db.execute(
            "SELECT 1 FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Ürün bulunamadı")
        rows = db.execute(
            """SELECT recorded_on, price FROM price_history
               WHERE product_id = ? AND recorded_on >= ?
               ORDER BY recorded_on ASC""",
            (product_id, start.isoformat()),
        ).fetchall()
    prices = [row["price"] for row in rows]
    return {
        "points": [dict(row) for row in rows],
        "highest": max(prices),
        "lowest": min(prices),
        "current": prices[-1],
    }


@app.get("/")
def home_page() -> FileResponse:
    return FileResponse(INDEX_FILE)


@app.get("/{full_path:path}")
def spa_fallback(full_path: str) -> FileResponse:
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="API rotası bulunamadı")
    if full_path in {"style.css", "app.js"}:
        requested_file = ROOT / full_path
        return FileResponse(requested_file)
    return FileResponse(INDEX_FILE)
