# -*- coding: utf-8 -*-
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

from travel_locale import default_currency_for_country, is_ru_or_cis_country, resolve_place_country
from value_normalization import normalized_search_value, truncate_source_prompt

RUS_MONTH_WORDS = [
    "январ", "феврал", "март", "апрел", "ма", "июн", "июл", "август", "сентябр", "октябр", "ноябр", "декабр",
    "весн", "лет", "осен", "зим", "майск", "новогод", "выходн",
]

INTEREST_KEYWORDS: dict[str, list[str]] = {
    "природа": ["природ", "вид", "море", "пляж", "гора", "парк", "лес", "остров", "поход", "закат"],
    "еда": ["еда", "гастро", "ресторан", "кафе", "кофе", "морепродукт", "рынок", "уличн", "бар"],
    "история": ["истори", "музе", "архитект", "крепост", "храм", "собор", "галере", "старый город"],
    "город": ["город", "центр", "улоч", "район", "атмосфер", "смотров", "набереж"],
    "спокойно": ["спокой", "без спеш", "медлен", "релакс", "тихо"],
    "активно": ["актив", "насыщ", "много всего", "ранний старт", "максимум"],
    "семья": ["дет", "семь", "ребен"],
}

BUDGET_HINTS = {
    "эконом": ["эконом", "дешев", "бюджетно", "минимум", "недорого"],
    "бизнес": ["бизнес", "business", "средн", "нормальн", "баланс", "умеренн", "комфорт"],
    "первый класс": ["первый класс", "first class", "премиум", "люкс", "vip", "вип", "не ограничен", "без ограничений"],
}

APPROX_RUB_RATES: dict[str, float] = {
    "RUB": 1.0,
    "EUR": 96.0,
    "USD": 89.0,
    "TRY": 2.35,
    "KZT": 0.18,
    "BYN": 27.0,
    "AMD": 0.23,
    "KGS": 1.02,
    "UZS": 0.007,
    "AZN": 52.0,
    "GEL": 33.0,
    "AED": 24.0,
    "THB": 2.45,
    "GBP": 113.0,
    "JPY": 0.60,
    "CNY": 12.3,
    "KRW": 0.064,
    "VND": 0.0035,
    "IDR": 0.0054,
    "INR": 1.07,
    "CZK": 3.9,
    "HUF": 0.24,
    "PLN": 22.5,
    "CHF": 101.0,
}


@dataclass(slots=True)
class TripRequest:
    title: str
    destination: str
    origin: str
    dates_text: str
    days_count: int
    group_size: int
    budget_text: str
    interests: list[str]
    notes: str
    source_prompt: str
    language_code: str = "ru"

    @property
    def interests_text(self) -> str:
        return ", ".join(self.interests) if self.interests else "не указаны"


@dataclass(slots=True)
class TripPlan:
    context_text: str
    itinerary_text: str
    logistics_text: str
    stay_text: str
    alternatives_text: str
    budget_breakdown_text: str
    budget_total_text: str


@dataclass(slots=True)
class BudgetInterpretation:
    display_text: str
    budget_class: str
    mode: str
    amount_value: int | None = None
    confidence: float = 0.0


@dataclass(slots=True)
class DestinationProfile:
    key: str
    display_name: str
    country: str
    currency: str
    best_season: str
    vibe: str
    quick_facts: list[str]
    hotel_areas: list[str]
    transport_notes: list[str]
    alternatives: list[str]
    place_groups: dict[str, list[str]]
    transport_range: tuple[int, int]
    lodging_per_night: tuple[int, int]
    food_per_day: tuple[int, int]
    local_per_day: tuple[int, int]
    activity_per_trip: tuple[int, int]
    aliases: tuple[str, ...] = ()


DESTINATIONS: list[DestinationProfile] = [
    DestinationProfile(
        key="владивосток",
        display_name="Владивосток",
        country="Россия",
        currency="RUB",
        best_season="июнь–сентябрь для моря и островных выездов; май и октябрь — для спокойного городского темпа",
        vibe="море, сопки, смотровые точки и сильная гастро-линия с морепродуктами",
        quick_facts=[
            "город рельефный: удобнее группировать точки по районам, а не бегать через весь день через весь город",
            "даже летом стоит заложить запас на ветер и переменчивую погоду",
            "если нужен день на природу, его лучше сразу отделять от плотного городского маршрута",
        ],
        hotel_areas=[
            "центр — для первого знакомства, набережной и вечерних прогулок",
            "район около набережной — если важны кафе, виды и быстрый доступ к центру",
            "Русский остров / более тихая локация — если в приоритете природа и размеренный темп",
        ],
        transport_notes=[
            "если едете издалека, базовый сценарий почти всегда начинается с самолета; сравнивайте ранний прилет и вечерний вылет",
            "внутри города удобно сочетать пешие участки с такси; под остров и дальние точки полезен автомобиль",
            "на день с Русским островом или видовыми точками лучше не ставить плотную вечернюю программу в центре",
        ],
        alternatives=[
            "Хабаровск — если хочется более простую городскую логистику и короткую насыщенную поездку",
            "Калининград — если нужен морской вайб и городская прогулка в более компактном формате",
            "Сочи — если хочется море и природу, но с более мягким климатом и большим числом отелей",
        ],
        place_groups={
            "default": [
                "прогулка по центру и Спортивной набережной",
                "смотровая точка на город и бухту",
                "фуникулер и спокойный обзорный маршрут по историческому центру",
                "Токаревский маяк или другой короткий выезд к воде",
            ],
            "природа": [
                "Русский остров и день с видовыми остановками",
                "маршрут по прибрежным точкам и смотровым площадкам",
                "легкая прогулка по островной части и закат у воды",
            ],
            "еда": [
                "ужин с морепродуктами и локальной кухней",
                "рынок / гастро-точка с быстрым знакомством с дальневосточным меню",
                "кофе-стоп и поздний ужин в живом городском районе",
            ],
            "история": [
                "музейная точка по морской истории или истории города",
                "короткий маршрут по старым кварталам и портовой истории",
            ],
            "город": [
                "вечерний центр с огнями, лестницами и смотровыми площадками",
                "район с кафе и прогулкой без жесткого тайминга",
            ],
        },
        transport_range=(22000, 38000),
        lodging_per_night=(2800, 5500),
        food_per_day=(1800, 3200),
        local_per_day=(500, 1100),
        activity_per_trip=(2200, 5000),
        aliases=("владик",),
    ),
    DestinationProfile(
        key="санкт-петербург",
        display_name="Санкт-Петербург",
        country="Россия",
        currency="RUB",
        best_season="май–сентябрь для белых ночей и длинных прогулок; зимой хорош для музеев и спокойного городского сценария",
        vibe="архитектура, музеи, вода и прогулки по слоям города",
        quick_facts=[
            "маршрут лучше собирать блоками по районам, иначе время уйдет на перемещения",
            "погода меняется быстро, поэтому полезно иметь indoor-план на один из дней",
            "вечерние прогулки работают лучше, если жилье находится недалеко от центра",
        ],
        hotel_areas=[
            "центр / Невский — если нужен плотный первый визит",
            "Петроградская сторона — если хочется более спокойного ритма и кафе",
            "районы у воды — если нужен красивый вечерний маршрут",
        ],
        transport_notes=[
            "для коротких поездок удобно прилетать или приезжать ранним утром, чтобы не терять первый день",
            "внутри города лучше строить маршрут вокруг метро и пеших блоков",
            "плотный музейный день не стоит смешивать с дальними выездами и длинными вечерними переходами",
        ],
        alternatives=[
            "Калининград — если нужен более компактный городской ритм",
            "Казань — если хочется сильной гастро-линии и города на 3–4 дня",
            "Стамбул — если нужен более яркий контраст культур и плотный городской вайб",
        ],
        place_groups={
            "default": [
                "Невский и первый обзорный маршрут по центру",
                "набережные и классическая прогулка по открыткам города",
                "спокойный вечер у воды и по красивым улицам",
                "одна большая архитектурная точка + соседний район без спешки",
            ],
            "природа": [
                "парк / островной маршрут с длинной прогулкой",
                "тихий зеленый блок с кофе и передышкой от центра",
            ],
            "еда": [
                "гастро-маршрут по кафе и рынкам",
                "вечер с локальной кухней и винным баром / коктейлями",
            ],
            "история": [
                "музейный день с одним главным музеем и одной легкой дополнительной точкой",
                "маршрут по дворцам, соборам и старым улицам",
            ],
            "город": [
                "Петроградская сторона или другой район с атмосферой города без туристической гонки",
                "вечерний маршрут по мостам и подсвеченным фасадам",
            ],
        },
        transport_range=(6000, 18000),
        lodging_per_night=(2500, 5200),
        food_per_day=(1700, 3200),
        local_per_day=(400, 900),
        activity_per_trip=(1800, 5000),
        aliases=("питер", "спб"),
    ),
    DestinationProfile(
        key="казань",
        display_name="Казань",
        country="Россия",
        currency="RUB",
        best_season="май–сентябрь для прогулок и выездов; зимой хорош для короткого city break",
        vibe="история, еда, религиозные и культурные слои в компактном формате",
        quick_facts=[
            "для 3–4 дней город раскрывается очень хорошо без перегруза",
            "удобно сочетать исторический центр, гастрономию и один спокойный выезд",
            "жилье в центре сильно упрощает короткую поездку",
        ],
        hotel_areas=[
            "центр — если нужен быстрый доступ к главным точкам",
            "район у набережной — если хотите более прогулочный сценарий",
            "спокойный квартал у центра — если важнее тишина, чем вечерний шум",
        ],
        transport_notes=[
            "на короткий срок лучше прилетать или приезжать максимально близко к началу дня",
            "по центру удобно ходить пешком с короткими переездами",
            "один насыщенный день лучше держать только под центр и гастро-точки",
        ],
        alternatives=[
            "Нижний Новгород — если нужен другой исторический город на 2–3 дня",
            "Санкт-Петербург — если хочется больше архитектуры и музеев",
            "Стамбул — если нужен более сильный культурный контраст",
        ],
        place_groups={
            "default": [
                "Кремль и первый круг по центру",
                "пешеходная улица и спокойная прогулка по главному маршруту",
                "вечерняя набережная и мягкий городской ритм",
            ],
            "еда": [
                "локальная кухня и гастро-точки с татарским акцентом",
                "чайная / десертный блок и расслабленный вечер",
            ],
            "история": [
                "старые кварталы, мечеть / храм и музейная точка",
                "спокойный маршрут по культурным слоям города",
            ],
            "природа": [
                "парк и прогулочный блок на полдня",
                "тихая зеленая пауза между историческими точками",
            ],
            "город": [
                "район с кафе и современной городской жизнью",
                "вечерний маршрут без перегруза по красивым улицам",
            ],
        },
        transport_range=(5000, 15000),
        lodging_per_night=(2200, 4800),
        food_per_day=(1500, 2800),
        local_per_day=(350, 800),
        activity_per_trip=(1200, 3500),
        aliases=(),
    ),
    DestinationProfile(
        key="калининград",
        display_name="Калининград",
        country="Россия",
        currency="RUB",
        best_season="май–сентябрь для городской прогулки и моря; межсезонье подходит для спокойного уикенда",
        vibe="компактный город, европейский ритм, море и короткие выезды",
        quick_facts=[
            "лучше сразу решать, нужен ли день на побережье — это влияет на темп всей поездки",
            "центр удобен для первого визита, но тихие кварталы дают более спокойное проживание",
            "поездку легко сделать или очень расслабленной, или довольно насыщенной",
        ],
        hotel_areas=[
            "центр — если хотите ходить пешком и не думать о логистике",
            "тихий квартал рядом с центром — если нужен спокойный ночной режим",
            "побережье или гибридный сценарий — если в приоритете море",
        ],
        transport_notes=[
            "на 3–5 дней стоит заранее решить, хотите ли отдельный день на море или держите поездку городской",
            "по городу удобен пеший сценарий с короткими переездами",
            "если группа большая, выгоднее сразу планировать апартаменты и трансферы",
        ],
        alternatives=[
            "Санкт-Петербург — если нужен более насыщенный городской слой",
            "Сочи — если хочется больше природы и моря",
            "Владивосток — если нужен более яркий морской характер и дальний маршрут",
        ],
        place_groups={
            "default": [
                "центральный прогулочный маршрут и первый обзор города",
                "район с водой, кафе и неспешным ритмом",
                "вечерняя прогулка по красивому кварталу",
            ],
            "природа": [
                "день на побережье или короткий морской выезд",
                "спокойный природный блок с закатной точкой",
            ],
            "еда": [
                "рыбный / локальный ужин и городской гастро-маршрут",
                "кофе и десертный блок в спокойном районе",
            ],
            "история": [
                "исторический музейный слой и маршрут по старым кварталам",
                "день с акцентом на архитектуру и локальную историю",
            ],
            "город": [
                "район с атмосферой и современными кафе",
                "медленная прогулка без гонки по обязательным точкам",
            ],
        },
        transport_range=(7000, 18000),
        lodging_per_night=(2500, 5200),
        food_per_day=(1600, 3000),
        local_per_day=(350, 900),
        activity_per_trip=(1500, 3800),
        aliases=(),
    ),
    DestinationProfile(
        key="сочи",
        display_name="Сочи",
        country="Россия",
        currency="RUB",
        best_season="май–октябрь для моря и прогулок; межсезонье хорошо для мягкого отдыха и коротких выездов",
        vibe="море, легкие выезды, прогулки и расслабленный темп",
        quick_facts=[
            "нужно заранее решить, что важнее: море, город или выезд в горы",
            "в высокий сезон логистика и цены ощутимо плотнее, чем в межсезонье",
            "насыщенный маршрут лучше собирать блоками по районам",
        ],
        hotel_areas=[
            "центр — если нужен баланс прогулок и логистики",
            "поближе к морю — если главная цель пляж и вечерняя набережная",
            "тихий район / апартаменты — если важна пауза и спокойный ритм",
        ],
        transport_notes=[
            "для 4–5 дней лучше выбирать один главный выезд, а не пытаться уместить все",
            "если цель — море, не перегружайте середину дня длинными переездами",
            "на группу из 4+ человек заранее продумайте трансфер и формат проживания",
        ],
        alternatives=[
            "Калининград — если хочется более спокойного моря и города",
            "Стамбул — если нужен городской ритм и еда вместо пляжного сценария",
            "Владивосток — если хочется более яркого морского характера и видовых точек",
        ],
        place_groups={
            "default": [
                "набережная и мягкий вход в город",
                "день с морем и прогулкой без спешки",
                "вечерний маршрут по кафе и набережной",
            ],
            "природа": [
                "один выезд в горный / природный блок",
                "тихий маршрут к воде или зеленой точке",
            ],
            "еда": [
                "гастро-вечер с локальной кухней",
                "поздний завтрак и спокойный ресторанный блок",
            ],
            "город": [
                "современный район с прогулкой и кофе",
                "неплотный городской день с акцентом на отдых",
            ],
            "история": [
                "одна культурная точка и прогулка по старому слою города",
            ],
        },
        transport_range=(6000, 20000),
        lodging_per_night=(2700, 6000),
        food_per_day=(1700, 3200),
        local_per_day=(450, 1000),
        activity_per_trip=(1700, 4200),
        aliases=(),
    ),
    DestinationProfile(
        key="стамбул",
        display_name="Стамбул",
        country="Турция",
        currency="TRY",
        best_season="апрель–июнь и сентябрь–ноябрь для прогулок; летом маршрут лучше делать мягче из-за жары",
        vibe="яркий городской контраст, еда, история, районы и длинные прогулки",
        quick_facts=[
            "город огромный, поэтому день лучше собирать вокруг одного-двух районов",
            "перемещения съедают время, если прыгать между берегами без плана",
            "для первого визита удобно держать жилье ближе к понятной транспортной связке",
        ],
        hotel_areas=[
            "район с простой транспортной связью — для первого визита и короткой поездки",
            "исторический центр — если хотите быть рядом с ключевыми точками, но готовы к большему потоку людей",
            "более спокойный район с кафе — если нужен живой, но не слишком туристический ритм",
        ],
        transport_notes=[
            "в день перелета и заселения лучше держать только один район и короткий вечерний маршрут",
            "для 4–5 дней эффективнее делить поездку по берегам / районам, а не по отдельным точкам",
            "обязательно держите запас по времени на дорогу и очереди в главных местах",
        ],
        alternatives=[
            "Казань — если хочется яркого культурного слоя, но проще по логистике",
            "Санкт-Петербург — если важнее архитектура и музеи в более знакомом формате",
            "Сочи — если хочется мягче по темпу и больше отдыха",
        ],
        place_groups={
            "default": [
                "первый маршрут по главному историческому слою города",
                "район с кафе и длинной прогулкой без жесткого тайминга",
                "видовая точка и вечер с городской атмосферой",
            ],
            "еда": [
                "гастро-маршрут с рынком / уличной едой и полноценным ужином",
                "чай / кофе, сладкое и неспешный вечерний блок",
            ],
            "история": [
                "день с историческими точками и музейным акцентом",
                "спокойный маршрут по старым улицам и культовым зданиям",
            ],
            "город": [
                "современный район с кафе, магазинами и вечерней жизнью",
                "другой берег / район для смены ритма без гонки",
            ],
            "природа": [
                "маршрут вдоль воды и расслабленный видовой день",
            ],
        },
        transport_range=(18000, 35000),
        lodging_per_night=(3500, 7000),
        food_per_day=(2200, 3800),
        local_per_day=(500, 1200),
        activity_per_trip=(2200, 5500),
        aliases=(),
    ),
]


DEFAULT_PROFILE = DestinationProfile(
    key="generic",
    display_name="Новая поездка",
    country="—",
    currency="LOCAL",
    best_season="зависит от направления; перед бронированием лучше отдельно проверить сезон и погоду",
    vibe="новый город, базовые точки, местная еда и один спокойный выезд",
    quick_facts=[
        "маршрут лучше собирать районами, а не списком разрозненных мест",
        "на поездку в 3–5 дней достаточно одного насыщенного и одного очень спокойного дня",
        "если едете группой, полезно заранее согласовать один общий бюджетный сценарий",
    ],
    hotel_areas=[
        "центр или район с хорошей логистикой — для первого визита",
        "тихий квартал рядом с центром — если важнее сон и меньше шума",
        "апартаменты для группы — если вас 4+ человека и нужен баланс бюджета",
    ],
    transport_notes=[
        "удобнее прилетать или приезжать ближе к началу дня, чтобы не потерять первый блок",
        "внутри города чаще всего выгоден сценарий: пешком + один вид локального транспорта",
        "не стоит ставить самые дальние точки в день прилета и вылета",
    ],
    alternatives=[
        "ближайший крупный город того же региона — если нужна проще логистика",
        "более компактное направление на 3 дня — если бюджет ограничен",
        "соседняя страна / другой город с тем же вайбом — если хочется контраста",
    ],
    place_groups={
        "default": [
            "исторический центр и первый обзорный круг",
            "главная прогулочная улица или набережная",
            "ключевая смотровая / архитектурная точка",
        ],
        "еда": [
            "рынок / гастро-точка и знакомство с локальной кухней",
            "вечерний ресторанный блок без спешки",
        ],
        "природа": [
            "парк, вода или природная точка на полдня",
            "закатная локация и спокойная прогулка",
        ],
        "история": [
            "главный музей / старый квартал / важная культурная точка",
            "маршрут по истории и архитектуре",
        ],
        "город": [
            "современный район с локальной жизнью",
            "спокойный вечерний маршрут по живому кварталу",
        ],
    },
    transport_range=(8000, 20000),
    lodging_per_night=(2300, 5000),
    food_per_day=(1500, 2900),
    local_per_day=(350, 850),
    activity_per_trip=(1500, 3500),
    aliases=(),
)


class TravelPlanner:
    _DESTINATION_STOPWORDS = {
        "аренду",
        "билет",
        "билеты",
        "бюджет",
        "выходные",
        "день",
        "дней",
        "дня",
        "дорога",
        "дорогу",
        "июле",
        "июня",
        "июнь",
        "комфорт",
        "летом",
        "маршрут",
        "машину",
        "несколько",
        "обратно",
        "отель",
        "отели",
        "пару",
        "поезд",
        "поездку",
        "поездки",
        "тогда",
        "туда",
        "экскурсии",
    }

    def parse_trip_request(self, text: str, *, fallback_title: str | None = None, language_code: str = "ru") -> TripRequest:
        cleaned = self._normalize_spaces(text)
        destination = self._extract_destination(cleaned)
        if not destination:
            raise ValueError(
                "Не смог понять направление. Напиши запрос так: '/plan Хочу на 5 дней во Владивосток, нас 4, бюджет Бизнес, любим море и еду'."
            )

        days_count = self._extract_days_count(cleaned)
        group_size = self._extract_group_size(cleaned)
        origin = self._extract_origin(cleaned)
        dates_text = self._extract_dates(cleaned)
        budget_text = self._extract_budget(cleaned)
        interests = self._extract_interests(cleaned)

        title = (fallback_title or "").strip() or f"{destination} • {days_count} дн. • {group_size} чел."

        return TripRequest(
            title=title,
            destination=destination,
            origin=origin or "не указано",
            dates_text=dates_text,
            days_count=days_count,
            group_size=group_size,
            budget_text=budget_text,
            interests=interests,
            notes=cleaned,
            source_prompt=truncate_source_prompt(cleaned),
            language_code="en" if language_code == "en" else "ru",
        )

    def build_request_from_fields(
        self,
        *,
        title: str,
        destination: str,
        origin: str,
        dates_text: str,
        days_count: int,
        group_size: int,
        budget_text: str,
        interests_text: str,
        notes: str,
        source_prompt: str = "",
        language_code: str = "ru",
    ) -> TripRequest:
        destination_clean = normalized_search_value(destination) or ""
        if not destination_clean:
            raise ValueError("Нужно указать направление поездки.")

        interests = self._extract_interests(interests_text) or self._split_interests(interests_text)
        return TripRequest(
            title=(title or f"{destination_clean} • {days_count} дн.").strip(),
            destination=self._display_destination(destination_clean),
            origin=normalized_search_value(origin) or "не указано",
            dates_text=normalized_search_value(dates_text) or "не указаны",
            days_count=max(1, int(days_count or 3)),
            group_size=max(1, int(group_size or 2)),
            budget_text=normalized_search_value(budget_text) or "бизнес",
            interests=interests,
            notes=(notes or "").strip(),
            source_prompt=truncate_source_prompt(source_prompt or notes or f"Поездка в {destination_clean}"),
            language_code="en" if language_code == "en" else "ru",
        )

    def generate_plan_heuristic(self, request: TripRequest) -> TripPlan:
        profile = self._find_profile(request.destination)
        budget_level = self._detect_budget_level(request.budget_text)
        context = self._build_context_text(profile, request, is_fallback=profile.key == "generic")
        itinerary = self._build_itinerary(profile, request)
        logistics = self._build_logistics_text(profile, request)
        stay = self._build_stay_text(profile, request)
        alternatives = self._build_alternatives_text(profile, request)
        budget_breakdown, budget_total = self._build_budget_text(profile, request, budget_level)
        return TripPlan(
            context_text=context,
            itinerary_text=itinerary,
            logistics_text=logistics,
            stay_text=stay,
            alternatives_text=alternatives,
            budget_breakdown_text=budget_breakdown,
            budget_total_text=budget_total,
        )

    def generate_plan(self, request: TripRequest) -> TripPlan:
        return self.generate_plan_heuristic(request)

    def profile_for(self, destination: str) -> DestinationProfile:
        return self._find_profile(destination)

    @staticmethod
    def _normalize_spaces(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()

    def _find_profile(self, destination: str) -> DestinationProfile:
        destination_lower = (normalized_search_value(destination) or destination or "").strip().lower()
        for profile in DESTINATIONS:
            if profile.key == destination_lower:
                return profile
            if destination_lower == profile.display_name.lower():
                return profile
            if destination_lower in {alias.lower() for alias in profile.aliases}:
                return profile
        for profile in DESTINATIONS:
            if profile.key in destination_lower or destination_lower in profile.key:
                return profile
            if any(alias.lower() in destination_lower for alias in profile.aliases):
                return profile
        generic = DEFAULT_PROFILE
        country = resolve_place_country(destination) or generic.country
        currency = default_currency_for_country(country)
        return DestinationProfile(
            key=generic.key,
            display_name=self._display_destination(destination),
            country=country,
            currency=currency,
            best_season=generic.best_season,
            vibe=generic.vibe,
            quick_facts=list(generic.quick_facts),
            hotel_areas=list(generic.hotel_areas),
            transport_notes=list(generic.transport_notes),
            alternatives=list(generic.alternatives),
            place_groups={key: list(value) for key, value in generic.place_groups.items()},
            transport_range=generic.transport_range,
            lodging_per_night=generic.lodging_per_night,
            food_per_day=generic.food_per_day,
            local_per_day=generic.local_per_day,
            activity_per_trip=generic.activity_per_trip,
            aliases=(),
        )

    def _extract_destination(self, text: str) -> str | None:
        lowered = text.lower()
        aliases: list[tuple[str, str]] = []
        for profile in DESTINATIONS:
            aliases.append((profile.key, profile.display_name))
            for alias in profile.aliases:
                aliases.append((alias, profile.display_name))
        aliases.sort(key=lambda item: len(item[0]), reverse=True)
        for alias, display in aliases:
            if alias.lower() in lowered:
                return display

        match = re.search(
            r"\b(?:в|во|на)\s+([A-Za-zА-Яа-яЁё\-]+(?:\s+[A-Za-zА-Яа-яЁё\-]+){0,2})",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        candidate = re.split(r"[,.;:!?]", match.group(1))[0].strip()
        candidate = re.sub(r"\b(на|дней|дня|день|человека|человек|чел)\b.*$", "", candidate, flags=re.IGNORECASE).strip()
        if not candidate:
            return None
        normalized_candidate = (normalized_search_value(candidate) or "").lower()
        if (
            not normalized_candidate
            or normalized_candidate in self._DESTINATION_STOPWORDS
            or normalized_candidate.startswith("куда")
        ):
            return None
        return self._display_destination(candidate)

    @staticmethod
    def _display_destination(text: str) -> str:
        text = text.strip()
        if not text:
            return text
        return " ".join(part[:1].upper() + part[1:] for part in text.split())

    @staticmethod
    def _extract_days_count(text: str) -> int:
        range_match = re.search(
            r"\b(?:на\s+)?(\d{1,2})\s*(?:-|–|—|до)\s*(\d{1,2})\s*(?:дн(?:я|ей)?|сут(?:ок)?|ноч(?:ь|и|ей)?)",
            text,
            flags=re.IGNORECASE,
        )
        if range_match:
            value = min(int(range_match.group(1)), int(range_match.group(2)))
            return max(1, min(value, 14))
        patterns = [
            r"\bна\s+(\d{1,2})\s*(?:дн(?:я|ей)?|сут(?:ок)?|ноч(?:ь|и|ей)?)",
            r"\b(\d{1,2})\s*(?:дн(?:я|ей)?|сут(?:ок)?|ноч(?:ь|и|ей)?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = int(match.group(1))
                return max(1, min(value, 14))
        return 3

    @staticmethod
    def _extract_group_size(text: str) -> int:
        lowered = text.lower()
        for value, patterns in {
            1: [r"\bя\s+буду\s+один\b", r"\bя\s+буду\s+одна\b", r"\bбуду\s+один\b", r"\bбуду\s+одна\b", r"\bодин\b", r"\bодна\b", r"\bсам\b", r"\bсама\b"],
            2: [r"\bвдвоем\b", r"\bвдвоём\b", r"\bнас\s+двое\b"],
            3: [r"\bвтроем\b", r"\bвтроём\b", r"\bнас\s+трое\b"],
        }.items():
            if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns):
                return value
        patterns = [
            r"\bнас\s+(\d{1,2})\b",
            r"\bмы\s+(\d{1,2})\b",
            r"\b(\d{1,2})\s*(?:чел(?:овек)?|человека|человек)\b",
            r"\bкомпан(?:ия|ией)\s+из\s+(\d{1,2})\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return max(1, min(int(match.group(1)), 20))
        if (
            any(token in lowered for token in ("хочу", "поеду", "лечу", "еду", "собираюсь", "планирую"))
            and not any(token in lowered for token in ("мы", "нас ", "с друзьями", "с семь", "вдвоем", "втроем"))
        ):
            return 1
        return 2

    @staticmethod
    def _extract_origin(text: str) -> str | None:
        match = re.search(
            r"\bиз\s+([A-Za-zА-Яа-яЁё\-]+(?:\s+[A-Za-zА-Яа-яЁё\-]+){0,2})",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        candidate = re.split(r"[,.;:!?]", match.group(1))[0].strip()
        candidate = re.sub(r"\b(на|в|во)\b.*$", "", candidate, flags=re.IGNORECASE).strip()
        candidate = re.sub(
            r"\b(я|мы|буду|будем|один|одна|вдвоем|вдвоём|втроем|втроём|сам|сама|подешевле|дешевле)\b.*$",
            "",
            candidate,
            flags=re.IGNORECASE,
        ).strip()
        return candidate or None

    @staticmethod
    def _extract_dates(text: str) -> str:
        numeric_range = re.search(
            r"\b(?:с\s*)?(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\s*(?:по|до|-|–|—)\s*(\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?)\b",
            text,
            flags=re.IGNORECASE,
        )
        if numeric_range:
            return f"{numeric_range.group(1)} - {numeric_range.group(2)}"

        numeric_single = re.search(
            r"\b\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?\b",
            text,
            flags=re.IGNORECASE,
        )
        if numeric_single:
            return numeric_single.group(0)

        direct = re.search(
            r"\b\d{1,2}\s*(?:-|–|—|до)?\s*\d{0,2}\s*(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)",
            text,
            flags=re.IGNORECASE,
        )
        if direct:
            return direct.group(0)

        season_or_month = re.search(
            r"\b(январ[ьяе]?|феврал[ьяе]?|март[ае]?|апрел[ьяе]?|ма[йяе]?|июн[ьяе]?|июл[ьяе]?|август[ае]?|сентябр[ьяе]?|октябр[ьяе]?|ноябр[ьяе]?|декабр[ьяе]?|весн[аойе]?|лет[оам]?|осен[ьюия]?|зим[аойе]?|майск(?:ие|их)?|новогодн(?:ие|их)?|выходн(?:ые|ых)?)\b",
            text,
            flags=re.IGNORECASE,
        )
        if season_or_month:
            return season_or_month.group(1)

        return "не указаны"

    def _extract_budget(self, text: str) -> str:
        return self.interpret_budget_text(text).display_text

    def interpret_budget_text(self, text: str) -> BudgetInterpretation:
        return self._interpret_budget_heuristic(text)

    def _interpret_budget_heuristic(self, text: str) -> BudgetInterpretation:
        lowered = (text or "").lower()

        if any(
            phrase in lowered
            for phrase in (
                "не ограничен",
                "не ограничена",
                "не ограничены",
                "без ограничений",
                "бюджет не важен",
                "любой бюджет",
                "без лимита",
                "дорого и красиво",
                "на максимум",
            )
        ):
            return BudgetInterpretation("без ограничений", "первый класс", "unlimited", confidence=0.98)

        if any(phrase in lowered for phrase in ("первый класс", "first class", "люкс", "премиум", "vip", "вип")):
            return BudgetInterpretation("Первый класс", "первый класс", "class_only", confidence=0.95)
        if any(phrase in lowered for phrase in ("бизнес", "business", "комфортно", "нормально, но без роскоши", "хороший отель")):
            return BudgetInterpretation("Бизнес", "бизнес", "class_only", confidence=0.85)
        if any(
            phrase in lowered
            for phrase in (
                "эконом",
                "economy",
                "подешевле",
                "дёшево",
                "дешево",
                "недорого",
                "бюджетно",
                "минимум трат",
                "подешевле бы",
            )
        ):
            return BudgetInterpretation("Эконом", "эконом", "class_only", confidence=0.9)

        numeric = self._extract_budget_number(lowered)
        if numeric is not None:
            if re.search(r"\bдо\s+\d", lowered):
                return BudgetInterpretation(f"до {self._format_budget_amount(numeric)} ₽", self._classify_budget_amount(numeric), "ceiling", numeric, 0.95)
            if re.search(r"\bна\s+\d", lowered):
                return BudgetInterpretation(f"на {self._format_budget_amount(numeric)} ₽", self._classify_budget_amount(numeric), "target", numeric, 0.95)
            if re.search(r"\b(?:от|больше|минимум)\s+\d", lowered):
                return BudgetInterpretation(f"от {self._format_budget_amount(numeric)} ₽", self._classify_budget_amount(numeric), "floor", numeric, 0.93)
            if any(token in lowered for token in ("примерно", "около", "~", "где-то")):
                return BudgetInterpretation(f"около {self._format_budget_amount(numeric)} ₽", self._classify_budget_amount(numeric), "approx", numeric, 0.92)
            return BudgetInterpretation(f"около {self._format_budget_amount(numeric)} ₽", self._classify_budget_amount(numeric), "approx", numeric, 0.78)

        for label, keywords in BUDGET_HINTS.items():
            if any(keyword in lowered for keyword in keywords):
                display = label.title() if label != "первый класс" else "Первый класс"
                return BudgetInterpretation(display, label, "class_only", confidence=0.7)

        return BudgetInterpretation("Бизнес", "бизнес", "class_only", confidence=0.3)

    @staticmethod
    def _extract_budget_number(text: str) -> int | None:
        normalized = (text or "").replace("\xa0", " ")
        compact_k = re.search(r"\b(\d{1,3})\s*(к|k)\b", normalized, flags=re.IGNORECASE)
        if compact_k:
            return int(compact_k.group(1)) * 1000
        thousands = re.search(r"\b(\d{1,3})\s*(?:тыс|тысяч)\b", normalized, flags=re.IGNORECASE)
        if thousands:
            return int(thousands.group(1)) * 1000
        plain = re.search(r"\b(\d[\d\s]{3,})\b", normalized)
        if plain:
            digits = int(re.sub(r"\s+", "", plain.group(1)))
            return digits
        return None

    @staticmethod
    def _format_budget_amount(value: int) -> str:
        return f"{int(value):,}".replace(",", " ")

    @staticmethod
    def _classify_budget_amount(value: int) -> str:
        if value <= 40000:
            return "эконом"
        if value >= 120000:
            return "первый класс"
        return "бизнес"

    def _extract_interests(self, text: str) -> list[str]:
        lowered = text.lower()
        result: list[str] = []
        for interest, keywords in INTEREST_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                result.append(interest)
        return result

    def _split_interests(self, text: str) -> list[str]:
        raw_items = [item.strip() for item in re.split(r"[,;/]| и ", text or "")]
        return [item for item in raw_items if item]

    def _detect_budget_level(self, budget_text: str) -> str:
        return self.interpret_budget_text(budget_text).budget_class

    def _build_context_text(
        self,
        profile: DestinationProfile,
        request: TripRequest,
        is_fallback: bool = False,
    ) -> str:
        lines: list[str] = []
        if is_fallback:
            if not is_ru_or_cis_country(profile.country):
                lines.append(
                    "⚠️ Маршрут собран по общему шаблону - для реальных мест и достопримечательностей "
                    "добавьте OPENROUTER_API_KEY в настройки бота."
                )
            else:
                lines.append(
                    "⚠️ Направление не в базе бота, поэтому ниже собран общий шаблон поездки. "
                    "Перед бронированием лучше перепроверить конкретные места и цены."
                )
        lines.extend([
            f"• Направление: {profile.display_name}, {profile.country}",
            f"• Формат поездки: {profile.vibe}",
            f"• Лучший сезон: {profile.best_season}",
            f"• Валюта / базовая расчетная единица: {profile.currency}",
        ])
        for fact in profile.quick_facts[:2]:
            lines.append(f"• Важно: {fact}")
        return "\n".join(lines)

    def _build_itinerary(self, profile: DestinationProfile, request: TripRequest) -> str:
        days = max(1, min(request.days_count, 10))
        queue = self._place_queue(profile, request.interests, days * 3)
        day_headers = [
            "Мягкий старт и знакомство",
            "Главные места без перегруза",
            "День с фокусом на интересы",
            "Смена темпа и районов",
            "Свободный блок и финальные точки",
            "Запасной сценарий и спокойный ритм",
            "Второй большой выезд / глубокий район",
            "День без спешки",
            "Финальный круг по любимым местам",
            "Выезд и мягкое завершение",
        ]

        lines: list[str] = []
        for day in range(days):
            slots = queue[day * 3 : day * 3 + 3]
            while len(slots) < 3:
                slots.append("спокойная прогулка по району проживания и пауза без жесткого тайминга")
            lines.append(f"День {day + 1}. {day_headers[min(day, len(day_headers) - 1)]}")
            lines.append(f"• Утро: {slots[0]}")
            lines.append(f"• День: {slots[1]}")
            lines.append(f"• Вечер: {slots[2]}")
            if day != days - 1:
                lines.append("")
        return "\n".join(lines)

    def _place_queue(self, profile: DestinationProfile, interests: list[str], target_count: int) -> list[str]:
        ordered_groups = ["default"]
        ordered_groups.extend(interest for interest in interests if interest in profile.place_groups and interest not in ordered_groups)
        ordered_groups.extend(group for group in ["город", "еда", "история", "природа", "default"] if group in profile.place_groups and group not in ordered_groups)

        queue: list[str] = []
        seen: set[str] = set()
        round_index = 0
        while len(queue) < target_count and round_index < 10:
            for group_name in ordered_groups:
                items = profile.place_groups.get(group_name, [])
                if round_index < len(items):
                    item = items[round_index]
                    if item not in seen:
                        queue.append(item)
                        seen.add(item)
                        if len(queue) >= target_count:
                            break
            round_index += 1
        if len(queue) < target_count:
            queue.extend(["локальная точка рядом с жильем и свободное время"] * (target_count - len(queue)))
        return queue

    def _build_logistics_text(self, profile: DestinationProfile, request: TripRequest) -> str:
        lines = [f"• Базовая логика: {note}" for note in profile.transport_notes]
        if request.origin != "не указано":
            lines.append(
                f"• Из {request.origin} проверьте минимум два сценария дороги: быстрый и самый выгодный. Для группы полезно сравнить общий трансфер и самостоятельный доезд."
            )
        else:
            lines.append("• Город выезда не указан, поэтому транспорт считаю только как ориентир без live-цен и без выбора конкретного рейса.")
        if request.days_count <= 3:
            lines.append("• Поездка короткая: лучше держать один большой городской блок в день и не распыляться на дальние точки.")
        elif request.days_count >= 5:
            lines.append("• Поездка длиннее 5 дней: полезно оставить один полудень пустым под погоду, отдых или перенос маршрута.")
        return "\n".join(lines)

    def _build_stay_text(self, profile: DestinationProfile, request: TripRequest) -> str:
        lines = []
        primary_area = profile.hotel_areas[0]
        if "спокойно" in request.interests and len(profile.hotel_areas) > 1:
            primary_area = profile.hotel_areas[min(1, len(profile.hotel_areas) - 1)]
        if "природа" in request.interests and len(profile.hotel_areas) > 2:
            primary_area = profile.hotel_areas[2]
        lines.append(f"• Базовый выбор: {primary_area}")
        for area in profile.hotel_areas[1:3]:
            if area != primary_area:
                lines.append(f"• Альтернатива: {area}")
        if request.group_size >= 4:
            lines.append("• Для группы 4+ часто выгоднее апартаменты или семейный номер рядом с понятной логистикой, чем несколько случайных маленьких номеров.")
        else:
            lines.append("• Для 1–3 человек лучше держаться ближе к первой линии логистики, чтобы не терять время на дорогу до центра.")
        return "\n".join(lines)

    def _build_alternatives_text(self, profile: DestinationProfile, request: TripRequest) -> str:
        lines = [f"• {item}" for item in profile.alternatives]
        if self._detect_budget_level(request.budget_text) == "эконом":
            lines.append("• Если бюджет жёсткий, выбирайте более компактное направление или сокращайте поездку на 1 день — так легче уложиться в проживание и локальные траты.")
        if "природа" in request.interests:
            lines.append("• Для природного сценария ищите направление, где природа доступна без длинных ежедневных переездов — это сильно экономит время и силы.")
        return "\n".join(lines)

    def _build_budget_text(self, profile: DestinationProfile, request: TripRequest, budget_level: str) -> tuple[str, str]:
        budget_meta = self.interpret_budget_text(request.budget_text)
        if profile.key == "generic" and profile.country not in {"—", "", None} and not is_ru_or_cis_country(profile.country):
            note = (
                f"Ориентир по бюджету для {profile.display_name} лучше проверять по live-ценам. "
                "Для международных направлений без локального профиля бот не будет придумывать грубую смету."
            )
            return (
                "\n".join(
                    [
                        note,
                        "• Сначала уточните жильё, транспорт и формат активностей.",
                        "• После этого проверьте цены по ссылкам в разделах билетов, жилья и дороги.",
                        "• Итого ориентир: нужна проверка цен в рублях по живым предложениям.",
                    ]
                ),
                "нужна проверка цен в рублях",
            )

        multiplier = {
            "эконом": 0.85,
            "бизнес": 1.1,
            "первый класс": 1.5,
        }[budget_level]

        nights = max(1, request.days_count - 1)
        group_size = max(1, request.group_size)
        shared_factor = 0.78 if group_size >= 2 else 1.0

        lodging_low, lodging_high = self._scale_range(profile.lodging_per_night, multiplier * shared_factor * nights * group_size)
        food_low, food_high = self._scale_range(profile.food_per_day, multiplier * request.days_count * group_size)
        local_low, local_high = self._scale_range(profile.local_per_day, multiplier * request.days_count * group_size)
        activities_low, activities_high = self._scale_range(profile.activity_per_trip, multiplier * group_size)

        transport_line = "• Дорога: город выезда не указан — пока без учета транспорта."
        transport_low = transport_high = 0
        if request.origin != "не указано":
            transport_low, transport_high = self._scale_range(profile.transport_range, multiplier * group_size)
            transport_line = (
                f"• Дорога из {request.origin}: {self._format_money(transport_low, profile.currency)} – {self._format_money(transport_high, profile.currency)}"
                " (грубая оценка без live-цен)"
            )

        total_low = lodging_low + food_low + local_low + activities_low + transport_low
        total_high = lodging_high + food_high + local_high + activities_high + transport_high
        local_currency = (profile.currency or "RUB").upper()
        total_low_rub = self._convert_to_rub(total_low, local_currency)
        total_high_rub = self._convert_to_rub(total_high, local_currency)

        header = (
            f"Ориентир на {group_size} чел. / {request.days_count} дн."
            f" — класс '{budget_meta.display_text if budget_meta.mode == 'class_only' else budget_meta.budget_class.title()}' без live-цен и без бронирований."
        )
        lines = [
            header,
            f"• Как понял бюджет: {budget_meta.display_text}",
            f"• Формат трат: {self._budget_style_note(budget_level)}",
            self._format_budget_line("Дорога", transport_low, transport_high, local_currency, suffix=" (грубая оценка без live-цен)") if request.origin != "не указано" else "• Дорога: город выезда не указан — пока без учета транспорта.",
            self._format_budget_line("Проживание", lodging_low, lodging_high, local_currency),
            self._format_budget_line("Еда", food_low, food_high, local_currency),
            self._format_budget_line("Локальный транспорт", local_low, local_high, local_currency),
            self._format_budget_line("Активности / входные билеты", activities_low, activities_high, local_currency),
            f"• Итого ориентир: {self._format_money(total_low_rub, 'RUB')} – {self._format_money(total_high_rub, 'RUB')}",
        ]
        if local_currency != "RUB":
            lines.append(
                f"• В местной валюте: {self._format_money(total_low, local_currency)} – {self._format_money(total_high, local_currency)}"
            )
        total_line = f"{self._format_money(total_low_rub, 'RUB')} – {self._format_money(total_high_rub, 'RUB')}"
        return "\n".join(lines), total_line

    @staticmethod
    def _scale_range(values: tuple[int, int], scale: float) -> tuple[int, int]:
        low = int(math.floor(values[0] * scale / 100.0) * 100)
        high = int(math.ceil(values[1] * scale / 100.0) * 100)
        return low, high

    @staticmethod
    def _format_money(value: int, currency: str = "RUB") -> str:
        symbol = {
            "RUB": "₽",
            "EUR": "€",
            "USD": "$",
            "TRY": "₺",
            "KZT": "KZT",
            "BYN": "BYN",
            "AMD": "AMD",
            "KGS": "KGS",
            "UZS": "UZS",
            "AZN": "AZN",
            "GEL": "GEL",
            "AED": "AED",
            "THB": "THB",
            "GBP": "GBP",
            "JPY": "JPY",
            "CNY": "CNY",
            "KRW": "KRW",
            "VND": "VND",
            "IDR": "IDR",
            "INR": "INR",
            "CZK": "CZK",
            "HUF": "HUF",
            "PLN": "PLN",
            "CHF": "CHF",
        }.get((currency or "LOCAL").upper(), currency or "LOCAL")
        return f"{value:,.0f}".replace(",", " ") + f" {symbol}"

    @staticmethod
    def _convert_to_rub(value: int, currency: str) -> int:
        rate = APPROX_RUB_RATES.get((currency or "RUB").upper(), 1.0)
        return int(round(value * rate))

    def _format_budget_line(
        self,
        title: str,
        low: int,
        high: int,
        currency: str,
        *,
        suffix: str = "",
    ) -> str:
        low_rub = self._convert_to_rub(low, currency)
        high_rub = self._convert_to_rub(high, currency)
        line = f"• {title}: {self._format_money(low_rub, 'RUB')} – {self._format_money(high_rub, 'RUB')}"
        if (currency or "RUB").upper() != "RUB":
            line += f" (≈ {self._format_money(low, currency)} – {self._format_money(high, currency)})"
        return line + suffix

    @staticmethod
    def _budget_style_note(budget_level: str) -> str:
        notes = {
            "эконом": "экономный отдых: базовое жильё, осторожнее с такси и платными активностями, опора на пешие маршруты и недорогую еду",
            "бизнес": "комфортный отдых: хороший отель, часть перемещений на такси, кафе и рестораны среднего уровня, 1–2 платные активности",
            "первый класс": "свободный комфорт: сильнее упор на удобное жильё, прямые переезды, хорошие рестораны и платные активности без жёсткой экономии",
        }
        return notes.get(budget_level, notes["бизнес"])
