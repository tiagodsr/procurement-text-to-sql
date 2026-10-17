"""Generate a fully synthetic procurement database (SQLite).

Everything here is fictional: supplier names, prices, carts, contracts.
The data is deterministic (fixed seed) and uses only the standard library.

Usage (from the repo root):
    python scripts/build_db.py                # writes data/suppliers.db
    python scripts/build_db.py --seed 7       # different (but reproducible) data
    python scripts/build_db.py --out path/to/suppliers.db

Warning: the output file is deleted and rebuilt.
"""
import argparse
import math
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

AS_OF = date(2026, 10, 3)  # "today" inside the dataset
DATA_START = date(2023, 1, 2)
DATA_END = date(2026, 9, 30)

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "suppliers.db"

# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #

FX_TO_EUR = {
    "EUR": 1.0, "USD": 0.92, "GBP": 1.17, "CHF": 1.04,
    "PLN": 0.23, "CZK": 0.040, "CNY": 0.13,
}

# code, weight, cities, currency, legal suffix, region label (for sister entities)
COUNTRIES = [
    ("DE", 30, ["Munich", "Stuttgart", "Hamburg", "Cologne", "Leipzig", "Nuremberg"], "EUR", "GmbH", "Deutschland"),
    ("PT", 8, ["Porto", "Lisbon", "Braga"], "EUR", "Lda", "Portugal"),
    ("FR", 9, ["Lyon", "Paris", "Lille"], "EUR", "SAS", "France"),
    ("US", 10, ["Atlanta", "Chicago", "Houston"], "USD", "Inc.", "America"),
    ("CN", 7, ["Shenzhen", "Shanghai"], "CNY", "Co., Ltd", "China"),
    ("GB", 6, ["Birmingham", "Leeds"], "GBP", "Ltd", "UK"),
    ("PL", 6, ["Wroclaw", "Gliwice"], "PLN", "Sp. z o.o.", "Polska"),
    ("ES", 4, ["Barcelona", "Valencia"], "EUR", "S.L.", "Iberia"),
    ("IT", 5, ["Milan", "Turin"], "EUR", "S.r.l.", "Italia"),
    ("NL", 5, ["Rotterdam", "Eindhoven"], "EUR", "B.V.", "Benelux"),
    ("CZ", 3, ["Brno", "Ostrava"], "CZK", "s.r.o.", "CZ"),
    ("CH", 3, ["Zurich", "Basel"], "CHF", "AG", "Swiss"),
    ("AT", 2, ["Vienna", "Graz"], "EUR", "GmbH", "Austria"),
]

PLANTS = [
    (1, "Site 01", "DE", "Munich"), (2, "Site 02", "DE", "Leipzig"),
    (3, "Site 03", "PT", "Porto"), (4, "Site 04", "FR", "Lyon"),
    (5, "Site 05", "PL", "Gliwice"), (6, "Site 06", "GB", "Birmingham"),
    (7, "Site 07", "US", "Atlanta"), (8, "Site 08", "CN", "Shenzhen"),
]
PLANT_WEIGHTS = [22, 16, 12, 10, 12, 8, 12, 8]

DEPARTMENTS = ["IT", "Production", "Logistics", "Quality", "HR", "Facilities", "R&D", "Finance"]

# Category groups (parent categories)
GROUPS = {
    "indirect": ("Indirect Materials", "IND"),
    "technical": ("Technical Materials", "TEC"),
    "it": ("IT", "ITX"),
    "services": ("Services", "SRV"),
}

# key: (name, code, group, quantity range, weight, typical departments, items)
# item: (EN, DE, FR, base price in EUR)
CATEGORIES = {
    "it_hw": ("IT Hardware", "ITX-100", "it", (1, 15), 9, ["IT", "HR", "R&D"], [
        ("Business laptop 14 inch", "Business-Notebook 14 Zoll", "Ordinateur portable professionnel 14 pouces", 1150),
        ("USB-C docking station", "USB-C Dockingstation", "Station d'accueil USB-C", 165),
        ("Monitor 27 inch", "Monitor 27 Zoll", "Ecran 27 pouces", 260),
        ("Wireless keyboard and mouse set", "Kabelloses Tastatur-Maus-Set", "Ensemble clavier souris sans fil", 48),
        ("Noise cancelling headset", "Headset mit Gerauschunterdrueckung", "Casque a reduction de bruit", 120),
        ("Full HD webcam", "Full-HD-Webcam", "Webcam Full HD", 55),
        ("USB-C cable 2 m", "USB-C-Kabel 2 m", "Cable USB-C 2 m", 9.5),
        ("External SSD 1 TB", "Externe SSD 1 TB", "SSD externe 1 To", 95),
    ]),
    "security": ("Security & Authentication", "ITX-200", "it", (1, 60), 5, ["IT", "Facilities", "HR"], [
        ("Hardware security key FIDO2 USB-A NFC", "Hardware-Sicherheitsschluessel FIDO2 USB-A NFC", "Cle de securite materielle FIDO2 USB-A NFC", 48),
        ("Hardware security key FIDO2 USB-C NFC", "Hardware-Sicherheitsschluessel FIDO2 USB-C NFC", "Cle de securite materielle FIDO2 USB-C NFC", 55),
        ("Smart card reader USB", "Smartcard-Leser USB", "Lecteur de cartes a puce USB", 28),
        ("RFID access badge 13.56 MHz", "RFID-Zutrittskarte 13,56 MHz", "Badge d'acces RFID 13,56 MHz", 3.2),
        ("Laptop security cable lock", "Notebook-Sicherheitsschloss", "Cable antivol pour ordinateur portable", 24),
        ("Privacy screen filter 14 inch", "Blickschutzfilter 14 Zoll", "Filtre de confidentialite 14 pouces", 42),
        ("OTP hardware token", "OTP-Hardware-Token", "Jeton OTP materiel", 18),
        ("Biometric USB fingerprint reader", "USB-Fingerabdruckleser", "Lecteur d'empreintes USB", 39),
    ]),
    "tools": ("Hand Tools", "TEC-100", "technical", (1, 30), 12, ["Production", "Quality", "Facilities"], [
        ("Steel wire file cleaner", "Feilenreiniger Stahldraht", "Brosse nettoyante pour limes en acier", 2.6),
        ("Bit set 39 pieces with sockets", "Bit-Set 39-teilig mit Steckschluesseln", "Jeu d'embouts 39 pieces avec douilles", 38),
        ("General-purpose cutter knife 18 mm", "Universalmesser 18 mm", "Cutter universel 18 mm", 5.6),
        ("Snap-off blades 18 mm, pack of 10", "Abbrechklingen 18 mm, 10er-Pack", "Lames secables 18 mm, lot de 10", 6.1),
        ("Manual drill press vice", "Maschinenschraubstock manuell", "Etau de perceuse manuel", 42),
        ("Torque wrench 20-100 Nm", "Drehmomentschluessel 20-100 Nm", "Cle dynamometrique 20-100 Nm", 78),
        ("Combination pliers 180 mm", "Kombizange 180 mm", "Pince universelle 180 mm", 14),
        ("Insulated screwdriver set 7 pcs", "Schraubendreher-Set isoliert 7-tlg.", "Jeu de tournevis isoles 7 pieces", 36),
    ]),
    "ppe": ("Personal Protective Equipment", "IND-100", "indirect", (10, 300), 12, ["Production", "Logistics", "Facilities"], [
        ("Cut-resistant safety gloves size 9", "Schnittschutzhandschuhe Groesse 9", "Gants anti-coupure taille 9", 4.2),
        ("Anti-fog safety glasses", "Schutzbrille beschlagfrei", "Lunettes de protection antibuee", 5.5),
        ("Ear plugs, box of 200", "Gehoerschutzstoepsel, 200er-Box", "Bouchons d'oreille, boite de 200", 21),
        ("High-visibility vest class 2", "Warnweste Klasse 2", "Gilet haute visibilite classe 2", 4.9),
        ("Safety shoes S3 size 43", "Sicherheitsschuhe S3 Groesse 43", "Chaussures de securite S3 pointure 43", 62),
        ("Hard hat with chin strap", "Schutzhelm mit Kinnriemen", "Casque de chantier avec jugulaire", 12),
        ("Disposable nitrile gloves, box of 100", "Nitril-Einweghandschuhe, 100er-Box", "Gants nitrile jetables, boite de 100", 8.5),
        ("FFP2 respirator mask, box of 20", "FFP2-Atemschutzmaske, 20er-Box", "Masque respiratoire FFP2, boite de 20", 16),
    ]),
    "office": ("Office Supplies", "IND-200", "indirect", (5, 200), 14, ["HR", "Finance", "IT", "R&D"], [
        ("A4 copy paper 80g, 500 sheets", "Kopierpapier A4 80 g, 500 Blatt", "Papier copie A4 80 g, 500 feuilles", 4.9),
        ("Ballpoint pens blue, box of 50", "Kugelschreiber blau, 50er-Box", "Stylos a bille bleus, boite de 50", 11),
        ("Notebook A5 lined", "Notizbuch A5 liniert", "Cahier A5 ligne", 3.4),
        ("Sticky notes 76x76 mm, 12 pads", "Haftnotizen 76x76 mm, 12 Bloecke", "Notes adhesives 76x76 mm, 12 blocs", 9.2),
        ("Heavy-duty stapler", "Heftgeraet Heavy Duty", "Agrafeuse grande capacite", 19),
        ("Ring binder A4, pack of 10", "Ordner A4, 10er-Pack", "Classeur A4, lot de 10", 17),
        ("Whiteboard markers assorted, 8 pcs", "Whiteboard-Marker sortiert, 8 Stueck", "Marqueurs tableau blanc assortis, 8 pieces", 7.5),
        ("Mesh desk organiser", "Schreibtisch-Organizer Metallgitter", "Organiseur de bureau en metal", 13),
    ]),
    "lab": ("Lab & Test Equipment", "TEC-200", "technical", (1, 10), 5, ["Quality", "R&D", "Production"], [
        ("Digital multimeter CAT III", "Digitalmultimeter CAT III", "Multimetre numerique CAT III", 95),
        ("Digital caliper 150 mm", "Digitaler Messschieber 150 mm", "Pied a coulisse numerique 150 mm", 34),
        ("Infrared thermometer", "Infrarot-Thermometer", "Thermometre infrarouge", 45),
        ("Oscilloscope probe 100 MHz", "Oszilloskop-Tastkopf 100 MHz", "Sonde d'oscilloscope 100 MHz", 62),
        ("Calibrated torque screwdriver", "Drehmomentschraubendreher kalibriert", "Tournevis dynamometrique etalonne", 110),
        ("Anti-static wrist strap", "Antistatik-Armband", "Bracelet antistatique", 6.5),
        ("Digital pressure gauge 0-10 bar", "Digitales Manometer 0-10 bar", "Manometre numerique 0-10 bar", 88),
        ("Micrometer 0-25 mm", "Buegelmessschraube 0-25 mm", "Micrometre 0-25 mm", 52),
    ]),
    "packaging": ("Packaging", "IND-300", "indirect", (20, 2000), 10, ["Logistics", "Production"], [
        ("Cardboard box 400x300x200 mm", "Faltkarton 400x300x200 mm", "Carton 400x300x200 mm", 0.95),
        ("Bubble wrap roll 50 m", "Luftpolsterfolie Rolle 50 m", "Rouleau de film a bulles 50 m", 24),
        ("Pallet stretch film 500 mm", "Palettenstretchfolie 500 mm", "Film etirable pour palettes 500 mm", 7.4),
        ("Packaging tape 50 mm x 66 m, pack of 6", "Paketklebeband 50 mm x 66 m, 6er-Pack", "Ruban d'emballage 50 mm x 66 m, lot de 6", 9),
        ("ESD shielding bags 200x300 mm, 100 pcs", "ESD-Abschirmbeutel 200x300 mm, 100 Stueck", "Sacs antistatiques 200x300 mm, 100 pieces", 28),
        ("Euro pallet EPAL", "Europalette EPAL", "Palette europe EPAL", 11.5),
        ("Custom cut foam inserts", "Schaumstoffeinlagen Zuschnitt", "Calages mousse sur mesure", 3.8),
        ("PP strapping band 12 mm, 1000 m", "Umreifungsband PP 12 mm, 1000 m", "Feuillard PP 12 mm, 1000 m", 19),
    ]),
    "fasteners": ("Fasteners & Fittings", "TEC-300", "technical", (100, 5000), 8, ["Production", "Facilities"], [
        ("Hex bolt M8x40 A2 stainless, 100 pcs", "Sechskantschraube M8x40 A2, 100 Stueck", "Vis a tete hexagonale M8x40 inox A2, 100 pieces", 14),
        ("Hex nut M8 A2, 200 pcs", "Sechskantmutter M8 A2, 200 Stueck", "Ecrou hexagonal M8 A2, 200 pieces", 9),
        ("Flat washer M8, 500 pcs", "Unterlegscheibe M8, 500 Stueck", "Rondelle plate M8, 500 pieces", 7),
        ("Cable ties 200 mm black, 1000 pcs", "Kabelbinder 200 mm schwarz, 1000 Stueck", "Colliers de serrage 200 mm noirs, 1000 pieces", 11),
        ("Blind rivets 4.8x16, 500 pcs", "Blindnieten 4,8x16, 500 Stueck", "Rivets aveugles 4,8x16, 500 pieces", 13),
        ("Hose clamp 25-40 mm, 10 pcs", "Schlauchschelle 25-40 mm, 10 Stueck", "Collier de serrage 25-40 mm, 10 pieces", 8),
        ("Self-tapping screws 4.2x19, 1000 pcs", "Blechschrauben 4,2x19, 1000 Stueck", "Vis autotaraudeuses 4,2x19, 1000 pieces", 16),
        ("Threaded rod M10 1 m", "Gewindestange M10 1 m", "Tige filetee M10 1 m", 3.1),
    ]),
    "chem": ("Lubricants & Chemicals", "TEC-400", "technical", (2, 100), 6, ["Production", "Facilities", "Quality"], [
        ("Multipurpose grease cartridge 400 g", "Mehrzweckfett Kartusche 400 g", "Graisse multi-usage cartouche 400 g", 5.9),
        ("Brake cleaner spray 500 ml", "Bremsenreiniger Spray 500 ml", "Nettoyant freins spray 500 ml", 3.7),
        ("Thread locker medium strength 50 ml", "Schraubensicherung mittelfest 50 ml", "Frein filet moyen 50 ml", 14.5),
        ("Silicone spray 400 ml", "Silikonspray 400 ml", "Spray silicone 400 ml", 6.2),
        ("Isopropanol 99% 1 l", "Isopropanol 99 % 1 l", "Isopropanol 99 % 1 l", 9.8),
        ("Cutting oil 5 l", "Schneidoel 5 l", "Huile de coupe 5 l", 38),
        ("Penetrating oil spray 400 ml", "Kriechoel Spray 400 ml", "Degrippant spray 400 ml", 5.1),
        ("Hydraulic oil HLP 46, 20 l", "Hydraulikoel HLP 46, 20 l", "Huile hydraulique HLP 46, 20 l", 74),
    ]),
    "facility": ("Facility & Cleaning", "IND-400", "indirect", (5, 300), 10, ["Facilities", "HR"], [
        ("Microfibre cleaning cloths, 10 pcs", "Mikrofasertuecher, 10 Stueck", "Chiffons microfibres, 10 pieces", 7.5),
        ("Floor cleaner concentrate 5 l", "Bodenreiniger Konzentrat 5 l", "Nettoyant sols concentre 5 l", 14),
        ("Waste bags 120 l, roll of 25", "Muellsaecke 120 l, Rolle a 25", "Sacs poubelle 120 l, rouleau de 25", 6.2),
        ("Paper hand towels, carton of 3000", "Papierhandtuecher, Karton a 3000", "Essuie-mains papier, carton de 3000", 36),
        ("Liquid hand soap 5 l", "Fluessigseife 5 l", "Savon liquide pour les mains 5 l", 12),
        ("Disinfectant surface wipes, 100 pcs", "Flaechendesinfektionstuecher, 100 Stueck", "Lingettes desinfectantes surfaces, 100 pieces", 8.9),
        ("Toilet paper 3-ply, 48 rolls", "Toilettenpapier 3-lagig, 48 Rollen", "Papier toilette 3 plis, 48 rouleaux", 22),
        ("Coffee beans 1 kg", "Kaffeebohnen 1 kg", "Cafe en grains 1 kg", 15),
    ]),
    "software": ("Software & Licences", "ITX-300", "it", (1, 200), 4, ["IT", "R&D", "Finance"], [
        ("Project management software, annual licence per seat", "Projektmanagement-Software, Jahreslizenz pro Nutzer", "Logiciel de gestion de projet, licence annuelle par utilisateur", 110),
        ("CAD software licence, annual per seat", "CAD-Software-Lizenz, jaehrlich pro Arbeitsplatz", "Licence logiciel CAO, annuelle par poste", 2400),
        ("Endpoint antivirus licence, annual per device", "Endpoint-Virenschutz-Lizenz, jaehrlich pro Geraet", "Licence antivirus poste, annuelle par appareil", 28),
        ("Cloud storage subscription 1 TB per user", "Cloud-Speicher-Abo 1 TB pro Nutzer", "Abonnement stockage cloud 1 To par utilisateur", 96),
        ("Video conferencing licence, annual per host", "Videokonferenz-Lizenz, jaehrlich pro Host", "Licence visioconference, annuelle par hote", 140),
        ("Password manager business licence, annual per user", "Passwort-Manager Business-Lizenz, jaehrlich pro Nutzer", "Licence gestionnaire de mots de passe, annuelle par utilisateur", 36),
        ("Data visualisation tool licence, annual per user", "Datenvisualisierungs-Lizenz, jaehrlich pro Nutzer", "Licence outil de visualisation de donnees, annuelle par utilisateur", 520),
        ("Process mining platform seat, annual", "Process-Mining-Plattform Nutzerlizenz, jaehrlich", "Licence plateforme process mining, annuelle", 3800),
    ]),
    "logistics": ("Logistics & Services", "SRV-100", "services", (1, 50), 5, ["Logistics", "Facilities", "Quality"], [
        ("Express parcel shipment within EU, up to 5 kg", "Expresspaket EU-weit, bis 5 kg", "Colis express dans l'UE, jusqu'a 5 kg", 19),
        ("Pallet freight domestic", "Palettenversand national", "Transport de palette national", 85),
        ("On-site equipment calibration service", "Vor-Ort-Kalibrierung von Messmitteln", "Etalonnage d'equipements sur site", 210),
        ("Technician maintenance hour", "Wartungstechniker pro Stunde", "Heure de technicien de maintenance", 78),
        ("Technical translation per 1000 words", "Fachuebersetzung pro 1000 Woerter", "Traduction technique pour 1000 mots", 120),
        ("Document shredding service per container", "Aktenvernichtung pro Behaelter", "Destruction de documents par conteneur", 45),
        ("Forklift rental per day", "Staplermiete pro Tag", "Location de chariot elevateur par jour", 130),
        ("Hazardous waste disposal per 200 l drum", "Sonderabfallentsorgung pro 200-l-Fass", "Elimination de dechets dangereux par fut de 200 l", 95),
    ]),
}

CART_DESCRIPTIONS = {
    "it_hw": ["New hire IT equipment", "Laptop refresh", "Workstation accessories", "Hardware for remote team"],
    "security": ["MFA rollout hardware", "Access control consumables", "Security tokens for new joiners", "Badges and readers"],
    "tools": ["On-site tools for maintenance", "Workshop tool replenishment", "Tools for assembly line", "Hand tools for service team"],
    "ppe": ["Safety equipment restock", "PPE for shop floor", "Safety gear for new staff", "Protective equipment"],
    "office": ["Office supplies restock", "Stationery order", "Meeting room supplies", "Quarterly office consumables"],
    "lab": ["Measuring instruments", "Test bench equipment", "Lab equipment replacement", "Calibration tools"],
    "packaging": ["Packaging material for shipping", "Pallets and wrapping", "Outbound packaging", "ESD packaging for electronics"],
    "fasteners": ["Fasteners for assembly", "Bolts and nuts stock", "Hardware kit for maintenance", "Fixings and cable management"],
    "chem": ["Lubricants and cleaners", "Maintenance chemicals", "Workshop consumables", "Cleaning solvents"],
    "facility": ["Cleaning supplies", "Facility consumables", "Washroom supplies", "Kitchen and hygiene supplies"],
    "software": ["Software licences renewal", "Annual subscriptions", "New licences for team", "Tool licences"],
    "logistics": ["Freight and shipping", "Maintenance services", "Calibration and services", "External services"],
}

DESCRIPTORS = {
    "it_hw": "IT Solutions", "security": "Secure Systems", "tools": "Tools", "ppe": "Safety",
    "office": "Office", "lab": "Instruments", "packaging": "Packaging", "fasteners": "Fasteners",
    "chem": "Chemicals", "facility": "Hygiene", "software": "Software", "logistics": "Logistics",
}
GENERAL_DESCRIPTORS = ["Industrial Supply", "Trading", "Group", "Supply"]

NAME_FIRST = ["Vel", "Nor", "Tra", "Kor", "Lum", "Ax", "Pel", "Bra", "Zen", "Hel",
              "Orb", "Qua", "Dov", "Mer", "Sil", "Tal", "Ven", "Fyr", "Gra", "Ito"]
NAME_SECOND = ["trix", "dan", "vex", "mar", "tek", "lon", "ria", "sen", "bon", "dex",
               "pia", "nova", "tor", "lis", "gen"]

SPEC_SUFFIXES = ["PK 10", "SET", "1 PC", "BULK", "- NEW", "(REPLACEMENT)", "2 PACK"]

# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE exchange_rates (
    currency    TEXT PRIMARY KEY,
    rate_to_eur REAL NOT NULL
);

CREATE TABLE plants (
    plant_id     INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    country_code TEXT NOT NULL,
    city         TEXT NOT NULL
);

CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    code        TEXT NOT NULL,
    parent_id   INTEGER REFERENCES categories(category_id)
);

CREATE TABLE suppliers (
    supplier_id        INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    country_code       TEXT NOT NULL,
    city               TEXT,
    currency           TEXT NOT NULL REFERENCES exchange_rates(currency),
    status             TEXT NOT NULL CHECK (status IN ('active','inactive','blocked')),
    risk_rating        TEXT NOT NULL CHECK (risk_rating IN ('low','medium','high')),
    is_preferred       INTEGER NOT NULL CHECK (is_preferred IN (0,1)),
    payment_terms_days INTEGER NOT NULL,
    onboarded_at       TEXT NOT NULL
);

CREATE TABLE supplier_aliases (
    alias_id    INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    alias       TEXT NOT NULL
);

CREATE TABLE contracts (
    contract_id  TEXT PRIMARY KEY,
    supplier_id  INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    category_id  INTEGER NOT NULL REFERENCES categories(category_id),
    status       TEXT NOT NULL CHECK (status IN ('published','draft','expired','terminated')),
    start_date   TEXT NOT NULL,
    end_date     TEXT NOT NULL,
    discount_pct REAL NOT NULL
);

CREATE TABLE catalog_items (
    catalog_item_id  INTEGER PRIMARY KEY,
    supplier_id      INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    contract_id      TEXT REFERENCES contracts(contract_id),
    category_id      INTEGER NOT NULL REFERENCES categories(category_id),
    sku              TEXT NOT NULL,
    item_name        TEXT NOT NULL,
    item_description TEXT,
    unit_price       REAL NOT NULL,
    currency         TEXT NOT NULL REFERENCES exchange_rates(currency),
    active           INTEGER NOT NULL CHECK (active IN (0,1)),
    created_at       TEXT NOT NULL
);

CREATE TABLE shopping_carts (
    cart_id               INTEGER PRIMARY KEY,
    description           TEXT NOT NULL,
    plant_id              INTEGER NOT NULL REFERENCES plants(plant_id),
    requester_department  TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('completed','cancelled','rejected','in_approval')),
    created_at            TEXT NOT NULL,
    completed_at          TEXT
);

CREATE TABLE shopping_cart_items (
    line_id         INTEGER PRIMARY KEY,
    cart_id         INTEGER NOT NULL REFERENCES shopping_carts(cart_id),
    supplier_id     INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    category_id     INTEGER REFERENCES categories(category_id),
    contract_id     TEXT REFERENCES contracts(contract_id),
    item_description TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    unit_price      REAL NOT NULL,
    total_value     REAL NOT NULL,
    currency        TEXT NOT NULL REFERENCES exchange_rates(currency),
    promised_days   INTEGER,
    actual_days     INTEGER,
    created_at      TEXT NOT NULL
);
"""

POST_SCHEMA = """
CREATE INDEX idx_lines_cart       ON shopping_cart_items(cart_id);
CREATE INDEX idx_lines_supplier   ON shopping_cart_items(supplier_id);
CREATE INDEX idx_lines_category   ON shopping_cart_items(category_id);
CREATE INDEX idx_lines_created    ON shopping_cart_items(created_at);
CREATE INDEX idx_carts_created    ON shopping_carts(created_at);
CREATE INDEX idx_catalog_supplier ON catalog_items(supplier_id);
CREATE INDEX idx_contracts_supp   ON contracts(supplier_id);
CREATE INDEX idx_alias_supplier   ON supplier_aliases(supplier_id);

-- Convenience view: completed purchase lines with EUR-normalised amounts.
CREATE VIEW v_completed_lines AS
SELECT l.line_id, l.cart_id, c.created_at AS cart_created_at, c.completed_at,
       c.plant_id, c.requester_department,
       l.supplier_id, l.category_id, l.contract_id, l.item_description,
       l.quantity, l.unit_price, l.total_value, l.currency,
       ROUND(l.unit_price  * fx.rate_to_eur, 4) AS unit_price_eur,
       ROUND(l.total_value * fx.rate_to_eur, 2) AS total_value_eur,
       l.promised_days, l.actual_days
FROM shopping_cart_items l
JOIN shopping_carts c ON c.cart_id = l.cart_id
JOIN exchange_rates fx ON fx.currency = l.currency
WHERE c.status = 'completed';

-- Convenience view: per-supplier purchasing history (completed carts only).
CREATE VIEW v_supplier_summary AS
SELECT s.supplier_id, s.name, s.country_code, s.status, s.risk_rating, s.is_preferred,
       COUNT(DISTINCT v.cart_id)           AS completed_carts,
       COUNT(v.line_id)                    AS completed_lines,
       ROUND(SUM(v.total_value_eur), 2)    AS spend_eur,
       MAX(v.cart_created_at)              AS last_cart_date,
       ROUND(AVG(v.actual_days - v.promised_days), 2) AS avg_delay_days
FROM suppliers s
LEFT JOIN v_completed_lines v ON v.supplier_id = s.supplier_id
GROUP BY s.supplier_id;
"""

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def iso(d):
    return d.isoformat()


def rand_date(rng, lo, hi):
    return lo + timedelta(days=rng.randint(0, (hi - lo).days))


def weighted(rng, items, weights):
    return rng.choices(items, weights=weights, k=1)[0]


def log_uniform_int(rng, lo, hi):
    if lo == hi:
        return lo
    return max(lo, int(round(math.exp(rng.uniform(math.log(lo), math.log(hi))))))


def add_typo(rng, text):
    words = text.split(" ")
    idx = [i for i, w in enumerate(words) if len(w) > 4 and w.isalpha()]
    if not idx:
        return text
    i = rng.choice(idx)
    w = words[i]
    j = rng.randint(1, len(w) - 2)
    words[i] = w[:j] + w[j + 1] + w[j] + w[j + 2:]
    return " ".join(words)


def language_weights(country):
    if country in ("DE", "CH", "AT"):
        return [0.35, 0.60, 0.05]  # en, de, fr
    if country == "FR":
        return [0.40, 0.05, 0.55]
    return [0.80, 0.10, 0.10]


def noisy_description(rng, item, country):
    """Free-text line description as a requester might type it."""
    lang = weighted(rng, [0, 1, 2], language_weights(country))
    text = item[lang]
    if rng.random() < 0.55:
        text = text.upper()
    if rng.random() < 0.20:
        text = f"{text} {rng.choice(SPEC_SUFFIXES)}"
    if rng.random() < 0.05:
        text = add_typo(rng, text)
    return text


def inflation(d):
    return 1 + 0.035 * ((d - date(2023, 1, 1)).days / 365.0)


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #


def build(db_path, seed):
    rng = random.Random(seed)

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    conn.executemany("INSERT INTO exchange_rates VALUES (?,?)", FX_TO_EUR.items())
    conn.executemany("INSERT INTO plants VALUES (?,?,?,?)", PLANTS)

    # ---- categories -------------------------------------------------------
    group_ids = {}
    cat_rows = []
    cid = 1
    for gkey, (gname, gcode) in GROUPS.items():
        group_ids[gkey] = cid
        cat_rows.append((cid, gname, gcode, None))
        cid += 1
    cat_ids = {}
    for key, (name, code, group, *_rest) in CATEGORIES.items():
        cat_ids[key] = cid
        cat_rows.append((cid, name, code, group_ids[group]))
        cid += 1
    conn.executemany("INSERT INTO categories VALUES (?,?,?,?)", cat_rows)
    cat_keys = list(CATEGORIES.keys())

    # ---- suppliers --------------------------------------------------------
    n_base = 90
    n_generalists = 12
    n_sisters = 6

    all_bases = [a + b for a in NAME_FIRST for b in NAME_SECOND]
    rng.shuffle(all_bases)
    bases = all_bases[:n_base]

    specs = []  # dict per supplier (pre-DB)
    for i, base in enumerate(bases):
        if i < n_generalists:
            cats = rng.sample(cat_keys, rng.randint(4, 6))
            descriptor = rng.choice(GENERAL_DESCRIPTORS)
            kind = "generalist"
        else:
            main = cat_keys[(i - n_generalists) % len(cat_keys)]
            cats = [main]
            if rng.random() < 0.30:
                cats.append(rng.choice([k for k in cat_keys if k != main]))
            descriptor = DESCRIPTORS[main]
            kind = "specialist"
        specs.append({"base": base, "cats": cats, "descriptor": descriptor, "kind": kind, "sister": False})

    # sister entities: same brand in another country (ambiguous alias on purpose)
    for src in rng.sample(specs[n_generalists:], n_sisters):
        specs.append({**src, "sister": True, "sister_of": src["base"]})

    country_codes = [c[0] for c in COUNTRIES]
    country_weights = [c[1] for c in COUNTRIES]
    country_info = {c[0]: c for c in COUNTRIES}

    suppliers = []
    taken_names = set()
    for spec in specs:
        if spec["sister"]:
            primary = next(s for s in suppliers if s["base"] == spec["base"] and not s["sister"])
            ccode = rng.choice([c for c in country_codes if c != primary["country"]])
        else:
            ccode = weighted(rng, country_codes, country_weights)
        _, _, cities, currency, legal, region = country_info[ccode]
        if currency == "CNY" and rng.random() < 0.5:
            currency = "USD"
        if spec["sister"]:
            name = f"{spec['base']} {region} {legal}"
        else:
            name = f"{spec['base']} {spec['descriptor']} {legal}"
        while name in taken_names:
            name = name.replace(spec["base"], spec["base"] + "x", 1)
        taken_names.add(name)

        # life cycle
        roll = rng.random()
        if roll < 0.12:
            onboarded = rand_date(rng, date(2025, 1, 1), date(2026, 6, 30))
            status, active_to = "active", DATA_END
        elif roll < 0.21:
            onboarded = rand_date(rng, date(2015, 1, 1), date(2022, 12, 31))
            status, active_to = "inactive", rand_date(rng, date(2023, 6, 1), date(2025, 12, 31))
        elif roll < 0.26:
            onboarded = rand_date(rng, date(2015, 1, 1), date(2022, 12, 31))
            status, active_to = "blocked", rand_date(rng, date(2024, 1, 1), date(2026, 3, 31))
        else:
            onboarded = rand_date(rng, date(2015, 1, 1), date(2022, 12, 31))
            status, active_to = "active", DATA_END

        if status == "blocked":
            risk = "high"
        else:
            risk = weighted(rng, ["low", "medium", "high"], [60, 28, 12])
        preferred = int(status == "active" and risk == "low" and rng.random() < 0.30)

        suppliers.append({
            "base": spec["base"], "sister": spec["sister"], "name": name,
            "country": ccode, "city": rng.choice(cities) if rng.random() > 0.05 else None,
            "currency": currency, "status": status, "risk": risk, "preferred": preferred,
            "terms": rng.choice([30, 45, 60, 90]), "onboarded": onboarded,
            "active_from": max(DATA_START, onboarded), "active_to": active_to,
            "cats": list(spec["cats"]),
            "price_factor": min(1.4, max(0.75, rng.gauss(1.0, 0.12))),
            "popularity": min(12.0, rng.paretovariate(1.6)),
            "reliability": min(0.98, max(0.5, rng.gauss(0.82, 0.12))),
        })

    # make sure every category has >= 8 suppliers
    for key in cat_keys:
        have = [s for s in suppliers if key in s["cats"]]
        if len(have) < 8:
            extra = rng.sample([s for s in suppliers if key not in s["cats"] and not s["sister"]], 8 - len(have))
            for s in extra:
                s["cats"].append(key)

    for i, s in enumerate(suppliers, start=1):
        s["id"] = i
    conn.executemany(
        "INSERT INTO suppliers VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(s["id"], s["name"], s["country"], s["city"], s["currency"], s["status"], s["risk"],
          s["preferred"], s["terms"], iso(s["onboarded"])) for s in suppliers],
    )

    # aliases (sister entities share the brand alias -> ambiguous on purpose)
    alias_rows, aid = [], 1
    brand_has_sister = {s["base"] for s in suppliers if s["sister"]}
    for s in suppliers:
        aliases = set()
        if s["base"] in brand_has_sister or rng.random() < 0.40:
            aliases.add(s["base"])
        if rng.random() < 0.30:
            # strip the full legal suffix ("Sp. z o.o.", "Co., Ltd"), not just the last word
            aliases.add(s["name"].removesuffix(" " + country_info[s["country"]][4]))
        for a in sorted(aliases):  # sorted: set order depends on PYTHONHASHSEED
            alias_rows.append((aid, s["id"], a))
            aid += 1
    conn.executemany("INSERT INTO supplier_aliases VALUES (?,?,?)", alias_rows)

    sup_by_cat = {k: [s for s in suppliers if k in s["cats"]] for k in cat_keys}

    # ---- contracts --------------------------------------------------------
    def contract_status(start, end, supplier):
        if supplier["status"] == "blocked" and end >= supplier["active_to"]:
            return "terminated"
        if start > AS_OF:
            return "draft"
        if end < AS_OF:
            return "terminated" if rng.random() < 0.08 else "expired"
        return "published"

    contracts = []  # dicts
    cnum = 1

    def add_contract(s, key, start, end, discount):
        nonlocal cnum
        status = contract_status(start, end, s)
        c = {"id": f"CTR-{100000 + cnum}", "sid": s["id"], "cat": key, "status": status,
             "start": start, "end": end, "disc": discount}
        cnum += 1
        contracts.append(c)
        return c

    for s in suppliers:
        for key in s["cats"]:
            p = 0.35 if len(s["cats"]) > 3 else 0.45
            if rng.random() > p:
                continue
            start = rand_date(rng, date(2021, 6, 1), date(2025, 6, 30))
            end = start + timedelta(days=365 * rng.randint(1, 3))
            disc = round(rng.uniform(0, 15), 1)
            first = add_contract(s, key, start, end, disc)
            if first["status"] == "expired" and s["status"] == "active" and rng.random() < 0.5:
                nstart = end + timedelta(days=1)
                add_contract(s, key, nstart, nstart + timedelta(days=730), round(rng.uniform(0, 15), 1))
            if s["status"] == "active" and rng.random() < 0.04:
                dstart = rand_date(rng, date(2026, 11, 1), date(2027, 3, 31))
                add_contract(s, key, dstart, dstart + timedelta(days=730), round(rng.uniform(0, 15), 1))

    conn.executemany(
        "INSERT INTO contracts VALUES (?,?,?,?,?,?,?)",
        [(c["id"], c["sid"], cat_ids[c["cat"]], c["status"], iso(c["start"]), iso(c["end"]), c["disc"]) for c in contracts],
    )
    contracts_by = {}
    for c in contracts:
        contracts_by.setdefault((c["sid"], c["cat"]), []).append(c)

    # ---- catalog ----------------------------------------------------------
    catalog_rows, cat_item_id = [], 1
    for s in suppliers:
        if s["status"] == "inactive" and rng.random() < 0.7:
            continue
        rate = FX_TO_EUR[s["currency"]]
        lang_w = language_weights(s["country"])
        for key in s["cats"]:
            clist = contracts_by.get((s["id"], key), [])
            usable = [c for c in clist if c["status"] != "draft"]
            latest = max(usable, key=lambda c: c["start"]) if usable else None
            p_cat = 0.80 if latest else 0.15
            if rng.random() > p_cat:
                continue
            items = CATEGORIES[key][6]
            chosen = rng.sample(items, rng.randint(max(1, len(items) // 2), len(items)))
            for item in chosen:
                disc = latest["disc"] if latest else 0.0
                price_eur = item[3] * s["price_factor"] * (1 - disc / 100.0) * inflation(AS_OF) * rng.uniform(0.97, 1.03)
                lang = weighted(rng, [0, 1, 2], lang_w)
                name = item[lang]
                sku = f"{s['base'][:3].upper()}-{rng.randint(10000, 99999)}"
                active_p = 0.92 if (latest and latest["status"] == "published") else 0.55
                active = int(s["status"] == "active" and rng.random() < active_p)
                desc = f"{name} | SKU {sku} | lead time {rng.randint(2, 21)} days"
                created = rand_date(rng, max(DATA_START, s["active_from"]), DATA_END) if s["active_from"] < DATA_END else DATA_END
                catalog_rows.append((
                    cat_item_id, s["id"], latest["id"] if latest else None, cat_ids[key], sku,
                    name, desc, round(price_eur / rate, 2), s["currency"], active, iso(created),
                ))
                cat_item_id += 1
    conn.executemany("INSERT INTO catalog_items VALUES (?,?,?,?,?,?,?,?,?,?,?)", catalog_rows)

    # ---- carts & lines ----------------------------------------------------
    n_carts = 7200
    cat_weights = [CATEGORIES[k][4] for k in cat_keys]
    n_lines_choices = [1, 2, 3, 4, 5, 6]
    n_lines_weights = [35, 25, 18, 11, 7, 4]
    plant_ids = [p[0] for p in PLANTS]

    cart_rows, line_rows = [], []
    line_id = 1
    days_span = (DATA_END - DATA_START).days

    for n in range(n_carts):
        cart_id = 2000000001 + n
        # created date: weekdays only, slight growth toward the present
        while True:
            offset = int(days_span * (rng.random() ** 0.85))
            created = DATA_START + timedelta(days=offset)
            if created.weekday() < 5:
                break
        key = weighted(rng, cat_keys, cat_weights)
        candidates = [s for s in sup_by_cat[key] if s["active_from"] <= created <= s["active_to"]]
        if not candidates:
            continue
        main_supplier = weighted(rng, candidates, [s["popularity"] for s in candidates])

        plant_id = weighted(rng, plant_ids, PLANT_WEIGHTS)
        dept = rng.choice(CATEGORIES[key][5] if rng.random() < 0.75 else DEPARTMENTS)
        desc = rng.choice(CART_DESCRIPTIONS[key])
        r = rng.random()
        if r < 0.20:
            desc += f" - Site {plant_id:02d}"
        elif r < 0.30:
            desc += f" Q{(created.month - 1) // 3 + 1} {created.year}"
        if rng.random() < 0.15:
            desc = desc.lower()

        # status
        if (AS_OF - created).days <= 10:
            status = "in_approval"
        else:
            status = weighted(rng, ["completed", "cancelled", "rejected"], [88, 7, 5])
        completed_at = None
        if status == "completed":
            completed_at = created + timedelta(days=rng.randint(1, 12))
            if completed_at > AS_OF:
                completed_at = AS_OF

        cart_rows.append((cart_id, desc, plant_id, dept, status, iso(created), iso(completed_at) if completed_at else None))

        items_pool = CATEGORIES[key][6]
        k = min(len(items_pool), weighted(rng, n_lines_choices, n_lines_weights))
        qty_lo, qty_hi = CATEGORIES[key][3]
        for item in rng.sample(items_pool, k):
            s = main_supplier
            if rng.random() > 0.90:
                alt = [c for c in candidates if c is not main_supplier]
                if alt:
                    s = weighted(rng, alt, [c["popularity"] for c in alt])
            rate = FX_TO_EUR[s["currency"]]

            contract_id, disc = None, 0.0
            for c in contracts_by.get((s["id"], key), []):
                if c["status"] != "draft" and c["start"] <= created <= c["end"] and rng.random() < 0.80:
                    contract_id, disc = c["id"], c["disc"]
                    break

            qty = log_uniform_int(rng, qty_lo, qty_hi)
            price_eur = item[3] * s["price_factor"] * inflation(created) * rng.uniform(0.97, 1.03) * (1 - disc / 100.0)
            unit_price = round(price_eur / rate, 2)
            total = round(unit_price * qty, 2)

            promised = actual = None
            if status == "completed" and created <= date(2026, 9, 15):
                if key == "software":
                    promised = rng.randint(0, 1)
                elif key == "logistics":
                    promised = rng.randint(1, 5)
                else:
                    promised = rng.randint(3, 10)
                if s["country"] == "CN":
                    promised += rng.randint(10, 18)
                elif s["country"] == "US":
                    promised += rng.randint(3, 6)
                late = 0 if rng.random() < s["reliability"] else rng.randint(1, 9)
                early = -1 if (late == 0 and promised > 3 and rng.random() < 0.15) else 0
                actual = max(0, promised + late + early)

            category_id = cat_ids[key] if rng.random() > 0.04 else None
            line_rows.append((
                line_id, cart_id, s["id"], category_id, contract_id,
                noisy_description(rng, item, s["country"]),
                qty, unit_price, total, s["currency"], promised, actual, iso(created),
            ))
            line_id += 1

    conn.executemany("INSERT INTO shopping_carts VALUES (?,?,?,?,?,?,?)", cart_rows)
    conn.executemany("INSERT INTO shopping_cart_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", line_rows)

    conn.executescript(POST_SCHEMA)
    meta = {
        "as_of_date": iso(AS_OF), "data_from": iso(DATA_START), "data_to": iso(DATA_END),
        "seed": str(seed), "note": "Fully synthetic data. All names are fictional.",
    }
    conn.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
    conn.commit()

    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in
              ["suppliers", "supplier_aliases", "categories", "contracts", "catalog_items",
               "shopping_carts", "shopping_cart_items", "plants", "exchange_rates"]}
    conn.close()
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    counts = build(args.out, args.seed)
    print(f"Wrote {args.out} (seed={args.seed})")
    for table, n in counts.items():
        print(f"  {table:22s} {n:>7,}")


if __name__ == "__main__":
    main()
