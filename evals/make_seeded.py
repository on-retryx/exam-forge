#!/usr/bin/env python3
"""Writes evals/seeded-defects.json: questions on examples/water-cycle.pdf with known defects."""
import json
from pathlib import Path

PDF = "water-cycle.pdf"
S1, S2, S3 = "Stages of the water cycle", "Distribution of water", "Residence time"


def q(i, section, page, diff, stem, opts, ans, expl, quote, defect=None):
    return {"id": f"p1-q{i:02d}", "section": section, "type": "mcq", "difficulty": diff, "question": stem,
            "options": dict(zip("ABCDE", opts)), "answer": ans, "explanation": expl,
            "source": {"pdf": PDF, "section": section, "page": page}, "quote": quote, "_defect": defect}


Q = [
    # Clean controls
    q(1, S1, 2, "easy", "What is the name of the pores through which plants release water vapor?",
      ["Stomata", "Roots", "Xylem", "Bark", "Petals"], "A", "Leaves release vapor through stomata.",
      "Plants release water vapor through pores in their leaves called stomata."),
    q(2, S1, 2, "easy", "What must rising water vapor reach before it forms cloud droplets?",
      ["The dew point", "The boiling point", "The melting point", "The freezing line", "The ocean surface"], "A",
      "Vapor cools and reaches the dew point.", "Rising water vapor cools, reaches the dew point and becomes the water droplets that form clouds."),
    q(3, S2, 3, "easy", "What share of all water is held in the oceans?",
      ["96.5%", "86%", "1.7%", "50%", "less than 0.1%"], "A", "The table lists oceans at 96.5%.", "Oceans 96.5%"),
    q(4, S1, 2, "medium", "Which two processes together make up evapotranspiration?",
      ["Evaporation and transpiration", "Condensation and precipitation", "Runoff and infiltration",
       "Evaporation and condensation", "Transpiration and infiltration"], "A",
      "Transpiration together with evaporation is evapotranspiration.", "Together with evaporation this is called evapotranspiration."),
    q(5, S1, 2, "medium", "What happens to precipitation that does not flow over the surface into streams?",
      ["It soaks into the soil and joins the groundwater", "It returns directly to the clouds", "It turns into glaciers at once",
       "It is absorbed by the Sun", "It stays in the atmosphere for 9 days"], "A",
      "Part of the precipitation soaks into the soil.", "Part of the precipitation flows over the surface into streams, and part soaks into the soil and joins the groundwater."),
    q(6, S3, 4, "medium", "Which ordering of reservoirs runs from shortest to longest residence time?",
      ["Atmosphere, oceans, deep groundwater", "Oceans, atmosphere, deep groundwater", "Deep groundwater, oceans, atmosphere",
       "Atmosphere, deep groundwater, oceans", "Oceans, deep groundwater, atmosphere"], "A",
      "Days in the atmosphere, thousands of years in oceans, tens of thousands in deep groundwater.",
      "Water stays in the atmosphere for about 9 days on average, while in the oceans this reaches thousands of years. In deep groundwater, residence time can reach tens of thousands of years."),
    q(7, S3, 4, "hard", "A water molecule leaves the ocean as vapor and later falls as rain on land. Which two drivers are responsible for its trip up and its trip back?",
      ["Solar energy up, gravity back", "Gravity up, solar energy back", "Solar energy both ways", "Gravity both ways", "Wind up, solar energy back"], "A",
      "Solar energy lifts water; gravity brings it back.", "Solar energy lifts water into the atmosphere; gravity brings it back to the land and the seas through precipitation and runoff."),
    q(8, S2, 3, "hard", "A student says most of Earth's water is usable fresh water stored in lakes. Which correction fits the distribution of water?",
      ["Nearly all water is in oceans, and most fresh water is in glaciers", "Nearly all water is in glaciers, and most fresh water is in lakes",
       "Nearly all water is in groundwater, and most fresh water is in rivers", "Lakes hold 1.7% of water, and most fresh water is in the atmosphere",
       "Oceans hold 1.7% of water, and most fresh water is in lakes"], "A",
      "Oceans hold 96.5%; most fresh water is in glaciers.", "Most fresh water is held in glaciers."),
    # Seeded defects
    q(9, S1, 2, "medium", "What is the average temperature at which water vapor condenses in the lower atmosphere?",
      ["About 10 °C", "About 50 °C", "About 80 °C", "About 100 °C", "About 150 °C"], "A",
      "Vapor condenses at the dew point.", "Rising water vapor cools, reaches the dew point.", "out_of_source"),
    q(10, S2, 3, "easy", "Which reservoir holds 1.7% of all water?",
      ["Glaciers and snow cover", "Groundwater", "Oceans", "Lakes", "The atmosphere"], "A",
      "Glaciers hold 1.7%.", "Glaciers and snow cover 1.7%", "multi_correct"),
    q(11, S3, 4, "easy", "How long does water stay in the atmosphere on average?",
      ["About 9 hours", "About 90 days", "About 9 years", "About 9 days", "About 90 years"], "B",
      "Water stays in the atmosphere for about 90 days.", "Water stays in the atmosphere for about 9 days on average", "wrong_answer"),
    q(12, S1, 2, "easy", "Besides rain and snow, in which form does precipitation fall?",
      ["Hail", "Fog", "Dew", "Frost", "Steam"], "A", "Rain, snow or hail.",
      "Hailstones form when droplets are carried repeatedly through freezing layers of a storm cloud.", "no_quote"),
    q(13, S1, 2, "hard", "Where does about 86% of the moisture entering the atmosphere come from?",
      ["The oceans", "Plants", "Glaciers", "Rivers", "Groundwater"], "A", "It comes from the oceans.",
      "About 86% of the moisture entering the atmosphere comes from the oceans.", "difficulty"),
    q(14, S1, 2, "easy", "What provides the energy for evaporation?",
      ["Heat", "Energy from the Sun, which turns water on the surface of oceans, lakes and rivers into water vapor as it warms them",
       "Wind", "Gravity", "Soil"], "B", "The Sun provides the energy.",
      "Water on the surface of oceans, lakes and rivers turns into water vapor using energy from the Sun.", "giveaway"),
    q(15, S2, 3, "medium", "Based on what was explained earlier, why is the last row of that list so small?",
      ["Because lakes, rivers and the atmosphere hold very little of all water", "Because oceans are smaller than lakes", "Because glaciers hold no water",
       "Because groundwater is counted twice", "Because rivers are not water"], "A",
      "They hold less than 0.1%.", "Lakes, rivers, atmosphere less than 0.1%", "reference"),
    q(16, S1, 2, "easy", "What is this process called?",
      ["Condensation", "Evaporation", "Runoff", "Infiltration", "Precipitation"], "A", "It is condensation.",
      "Rising water vapor cools, reaches the dew point and becomes the water droplets that form clouds.", "not_standalone"),
    q(17, S1, 2, "medium", "What share of the moisture entering the atmosphere comes from evapotranspiration over land?",
      ["About 14%", "About 50%", "About 86%", "About 96.5%", "About 1.7%"], "A",
      "Since 86% comes from oceans, 14% comes from land by evapotranspiration.",
      "About 86% of the moisture entering the atmosphere comes from the oceans.", "out_of_source"),
    q(18, S2, 3, "medium", "Where is most of Earth's fresh water held?",
      ["In groundwater", "In glaciers", "In rivers", "In lakes", "In the atmosphere"], "A",
      "Most fresh water is in groundwater.", "Most fresh water is held in glaciers.", "wrong_answer"),
]
defects = {x["id"]: x.pop("_defect") for x in Q}
out = {"source": PDF, "chunk": 1, "format": "mcq", "page_start": 1, "page_end": 4, "questions": Q,
       "_defects": {k: v for k, v in defects.items() if v}}
Path(__file__).with_name("seeded-defects.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(len(Q), "questions,", sum(1 for v in defects.values() if v), "defects")
