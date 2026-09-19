"""
AxonBot Verified Benchmark Examples Catalog (15 Cases)
Содержит 15 проверенных на 100%/99.9% кейсов из реального лога бенчмарка (test_e2e_40stress_run_log.md).
Каждый кейс доступен на русском и английском языках.
"""

from dataclasses import dataclass

@dataclass
class ExampleCase:
    id: str
    category: str
    title_ru: str
    title_en: str
    question_ru: str
    question_en: str
    test_id: str

CATEGORIES = {
    "foundations": {
        "title_ru": "Фундаменты и грунт",
        "title_en": "Foundations & Earthworks"
    },
    "rebar": {
        "title_ru": "Армирование конструкций",
        "title_en": "Reinforcement Detailing"
    },
    "stairs_balconies": {
        "title_ru": "Лестницы и балконы",
        "title_en": "Stairs & Balconies"
    },
    "standards": {
        "title_ru": "Стандарты, маски и спецификации",
        "title_en": "Standards, Naming & Schedules"
    }
}

EXAMPLES: list[ExampleCase] = [
    # Категория 1: Фундаменты и грунт (4 кейса)
    ExampleCase(
        id="ex_dwg_import",
        category="foundations",
        title_ru="РН и категория грунта из DWG",
        title_en="Workset & Category for DWG Soil",
        question_ru="В какой рабочий набор кидать импорт DWG массива грунта и какую категорию для контекстной модели выбирать?",
        question_en="Which workset should be used for DWG soil import and which in-place model category should be selected?",
        test_id="ST-02"
    ),
    ExampleCase(
        id="ex_piles_cutoff",
        category="foundations",
        title_ru="Свайное поле: срубка и плагины",
        title_en="Pile Field: Head Cut-off & Tools",
        question_ru="У меня свайное поле на 800 свай, как их быстро раскидать по осям и какой параметр отвечает за срубку головки?",
        question_en="I have a pile field with 800 piles, how do I quickly distribute them along grid lines and what parameter controls the head cut-off?",
        test_id="ST-03"
    ),
    ExampleCase(
        id="ex_pit_prep",
        category="foundations",
        title_ru="Подготовка под приямок",
        title_en="Sump Pit Blinding Layer",
        question_ru="Как сделать бетонную подготовку под приямок переменной глубины многослойным семейством 230?",
        question_en="How to model concrete blinding for a variable depth sump pit using multilayer family 230?",
        test_id="ST-07"
    ),
    ExampleCase(
        id="ex_exp_joint",
        category="foundations",
        title_ru="Дефшов с гидрошпонкой в плите",
        title_en="Expansion Joint with Waterstop",
        question_ru="Как замоделировать деформационный шов в плите фундамента с гидрошпонкой по регламенту?",
        question_en="How to model an expansion joint with a waterstop in a foundation slab according to standards?",
        test_id="ST-08"
    ),

    # Категория 2: Армирование конструкций (5 кейсов)
    ExampleCase(
        id="ex_bent_rebar",
        category="rebar",
        title_ru="Гнутый стержень с отгибом",
        title_en="Bent Rebar with Custom Bend",
        question_ru="Как сделать гнутый стержень арматуры с нестандартным отгибом, чтобы он не слетал в спецификации формы?",
        question_en="How to model a bent rebar with a custom hook without losing shape parameters in schedules?",
        test_id="ST-09"
    ),
    ExampleCase(
        id="ex_mesh_role",
        category="rebar",
        title_ru="Назначение фоновой сетки плиты",
        title_en="Slab Top Rebar Mesh Role",
        question_ru="Какое назначение арматуры в параметрах экземпляра выбирать для верхней фоновой сетки перекрытия по регламенту?",
        question_en="What rebar role parameter in instance properties should be chosen when placing the top slab background mesh according to standard?",
        test_id="ST-11"
    ),
    ExampleCase(
        id="ex_starter_bars",
        category="rebar",
        title_ru="2D-выпуски арматуры на планах",
        title_en="2D Starter Bar Annotation Family",
        question_ru="Как по регламенту оформить выпуски арматуры из фундаментной плиты на планах и сечениях, какое семейство использовать?",
        question_en="Which family is used according to KR regulations to annotate rebar starter bar zones from foundation slabs on 2D plans?",
        test_id="ST-13"
    ),
    ExampleCase(
        id="ex_rebar_chairs",
        category="rebar",
        title_ru="Фиксаторы сеток «лягушки»",
        title_en="Bar Chairs / Spacers",
        question_ru="Каким семейством по регламенту моделировать лягушки (фиксаторы) и как их правильно размещать?",
        question_en="Which family is prescribed by regulations to model rebar chairs (bar supports) and how to place them on face?",
        test_id="ST-16"
    ),
    ExampleCase(
        id="ex_rebar_groups",
        category="rebar",
        title_ru="Основа арматуры внутри групп",
        title_en="Rebar Host Inside Model Groups",
        question_ru="Почему нельзя изменить основу отдельного арматурного стержня внутри группы и как решить эту проблему по регламенту?",
        question_en="Why is it forbidden to change the host of an individual rebar inside a group and how to solve it per regulations?",
        test_id="ST-35"
    ),

    # Категория 3: Лестницы и балконы (3 кейса)
    ExampleCase(
        id="ex_balcony_thermal",
        category="stairs_balconies",
        title_ru="Балконная плита с термовкладышем",
        title_en="Balcony Slab with Thermal Break",
        question_ru="Балконная плита с термовкладышами не стыкуется с плитой перекрытия, как её правильно посадить?",
        question_en="Balcony slab with thermal insulation breaks does not align with the floor slab, how to position it correctly?",
        test_id="ST-17"
    ),
    ExampleCase(
        id="ex_stair_landing",
        category="stairs_balconies",
        title_ru="Опирание лестничной площадки",
        title_en="Stair Landing: Bearing Options",
        question_ru="Как отключить опирание и шпонки балок у лестничной площадки 205, если она опирается на стену?",
        question_en="How to disable beam bearings and keys on stair landing family 205 if it rests on a wall?",
        test_id="ST-19"
    ),
    ExampleCase(
        id="ex_stair_view_template",
        category="stairs_balconies",
        title_ru="Шаблон вида опалубки лестниц",
        title_en="Stair Formwork View Template",
        question_ru="Каким шаблоном вида оформлять опалубочные разрезы монолитных лестниц?",
        question_en="Which view template must be used for formwork sections of cast-in-place monolithic stairs?",
        test_id="ST-22"
    ),

    # Категория 4: Стандарты, маски и спецификации (3 кейса)
    ExampleCase(
        id="ex_walls_naming_mask",
        category="standards",
        title_ru="Маска стен и пилонов КР",
        title_en="Walls & Columns Naming Mask",
        question_ru="Каковы правила именования и маска типоразмеров монолитных стен и пилонов в проекте?",
        question_en="What are the naming rules and naming mask for monolithic wall and column types in the project?",
        test_id="ST-25"
    ),
    ExampleCase(
        id="ex_inplace_mask",
        category="standards",
        title_ru="Маска имени модели в контексте",
        title_en="In-Place Model Naming Mask",
        question_ru="Если я создаю контекстную модель приямка или массива грунта, по какой маске нужно задать имя модели?",
        question_en="What naming mask is defined in KR naming rules for in-place models (such as pits or soil mass)?",
        test_id="ST-26"
    ),
    ExampleCase(
        id="ex_precast_filters",
        category="standards",
        title_ru="Фильтр сборняка в спецификациях",
        title_en="Precast Schedule Filtering",
        question_ru="Какие фильтры выставить в спецификации конструкций, чтобы отсеять монолит и оставить только сборняк?",
        question_en="Which filters must be configured in precast elements specification to filter out monolithic structures and keep only precast elements?",
        test_id="ST-30"
    )
]

def get_categories() -> dict:
    return CATEGORIES

def get_cases_by_category(category_key: str) -> list[ExampleCase]:
    return [c for c in EXAMPLES if c.category == category_key]

def get_case_by_id(case_id: str) -> ExampleCase | None:
    for c in EXAMPLES:
        if c.id == case_id:
            return c
    return None