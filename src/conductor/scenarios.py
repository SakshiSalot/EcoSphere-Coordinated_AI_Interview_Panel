"""Role-play scenarios — PS11 capability 7.

A scenario is the panel stopping the interview and putting the candidate INSIDE
the situation instead of asking about it. "Tell me about a time you handled a
difficult stakeholder" gets a rehearsed story. "I'm the stakeholder, and I've
just been told my feature slipped again — go" gets whatever they actually do.

The mechanism already existed: `launch_scenario` locks the floor to whoever
started it, so a role-play cannot be interrupted by another persona wandering
in with an unrelated question. What did not exist was any CONTENT for it to
launch, or any way for it to end — which is the more serious half.

THE LOCK HAD NO RELEASE. `_launch_scenario` set `active_scenario` and nothing
in the codebase ever cleared it, so the first scenario a persona started would
hold the floor for the rest of the interview. There are now two ways out, and
the second one is the one that matters:

  * `end_scenario`, called by the persona when the role-play resolves.
  * MAX_EXCHANGES, enforced by the conductor. A model that forgets to call the
    tool must not be able to take the interview hostage, and "the model will
    remember" is not a mechanism.

WHY EACH SCENARIO NAMES WHAT IT IS PROBING. The panel is scored on evidence,
and a role-play that is merely dramatic produces none. `probes` is what the
marking engine has to work with afterwards, and writing it down forces each
scenario to be about something.
"""

import logging
from dataclasses import dataclass, field

log = logging.getLogger("scenarios")

# How many candidate answers a role-play may hold the floor for. Four is two
# exchanges of pressure and a resolution — long enough to see how somebody
# behaves, short enough that it is still an interview.
MAX_EXCHANGES = 4


@dataclass(frozen=True)
class Scenario:
    id: str
    owner: str          # the persona whose job this is
    title: str
    setup: str          # what the interviewer says to open it
    stance: str         # how to play the character while it runs
    probes: tuple       # what it is evidence OF
    resolve_when: str   # how the interviewer knows to stop
    use_when: str = ""  # when this one is the right one to pick


SCENARIOS: tuple[Scenario, ...] = (
    # --- behavioural: Meera ------------------------------------------------
    Scenario(
        id="slipped_deadline",
        owner="behavioural",
        title="Telling a stakeholder the date has moved",
        setup=(
            "Let's try something different. I'm going to be your product "
            "stakeholder for a minute. You've just worked out the release will "
            "slip by three weeks, and I don't know yet. I'm in your one-to-one. "
            "Tell me."
        ),
        stance=(
            "Play the stakeholder as disappointed and pressed, not abusive. "
            "Push once on why nobody flagged it earlier. If they give you a "
            "date, ask what makes that one different from the last one. If "
            "they blame a person, ask what they did about it at the time."
        ),
        probes=("ownership", "communicating bad news", "handling pressure"),
        resolve_when=(
            "they have told you the impact, what they are doing about it, and "
            "what you can expect next — or three exchanges have passed"
        ),
        use_when="they describe a project that slipped, or talk about deadlines",
    ),
    Scenario(
        id="disagree_with_senior",
        owner="behavioural",
        title="Disagreeing with someone more senior",
        setup=(
            "Let me put you in a situation. I'm your tech lead, and I've just "
            "decided we're rewriting the service in a framework you think is "
            "the wrong call. The team is listening. What do you say to me?"
        ),
        stance=(
            "Hold your position for the first exchange — ask what specifically "
            "worries them. Concede only if they bring evidence rather than "
            "preference. If they go silent or defer immediately, ask what they "
            "would have said if the room were empty."
        ),
        probes=("conviction", "disagreeing constructively", "influence without authority"),
        resolve_when=(
            "they have made an argument and said what they would do if "
            "overruled — or three exchanges have passed"
        ),
        use_when="they mention conflict, code review disagreements, or team decisions",
    ),
    Scenario(
        id="teammate_underperforming",
        owner="behavioural",
        title="A teammate is quietly not delivering",
        setup=(
            "Try this with me. You've noticed someone on your team hasn't "
            "shipped anything in three weeks and is going quiet in standup. "
            "Nobody has said anything. I'm that teammate, and we're getting a "
            "coffee. Start wherever you like."
        ),
        stance=(
            "Be guarded but not hostile. Deflect the first approach with "
            "\"yeah, it's been a busy few weeks\". Open up only if they ask "
            "something specific rather than general, or make it safe to answer."
        ),
        probes=("empathy", "raising a hard subject", "judgement about escalation"),
        resolve_when="they have opened the subject and said what they would do next",
        use_when="they talk about mentoring, team health, or leading people",
    ),

    # --- customer: Dev -----------------------------------------------------
    Scenario(
        id="angry_customer_outage",
        owner="customer",
        title="Explaining an outage to the customer it hit",
        setup=(
            "Can I put you on the spot? I'm a customer. Your system was down "
            "for four hours yesterday, in the middle of our billing run. I "
            "don't know what a database is. Explain what happened to me."
        ),
        stance=(
            "Refuse jargon, plainly and immediately: if they say replica, "
            "queue, or failover, say you do not know what that means. Ask "
            "whether it will happen again. Ask what you should tell YOUR "
            "customers, who are also angry."
        ),
        probes=("plain language", "accountability", "customer empathy"),
        resolve_when=(
            "you understand what happened and what stops it recurring, in "
            "words you would use yourself"
        ),
        use_when="they describe an incident, an outage, or reliability work",
    ),
    Scenario(
        id="feature_that_missed",
        owner="customer",
        title="The feature they shipped did not help",
        setup=(
            "Let me be a customer for a moment. You shipped the thing my team "
            "asked for, and six weeks on we're still doing the job by hand. "
            "I'm not angry, I'm just confused. What went wrong?"
        ),
        stance=(
            "Do not accept a technical explanation — you cannot evaluate one. "
            "Describe the manual work you are still doing when pressed. If "
            "they blame your requirements, ask what they would ask next time."
        ),
        probes=("outcome over output", "curiosity about users", "taking feedback"),
        resolve_when="they have worked out what they missed and how they would find it earlier",
        use_when="they describe shipping a feature, or talk about requirements",
    ),

    # --- hiring manager: Kavya ---------------------------------------------
    Scenario(
        id="scope_negotiation",
        owner="hiring_manager",
        title="Cutting scope with two weeks left",
        setup=(
            "Let's make this concrete. I'm your manager. There are two weeks "
            "to the launch and it's clear not all of it lands. I want you to "
            "tell me what we cut. I'll push back on the first thing you pick."
        ),
        stance=(
            "Push back once on whatever they cut first, with a business reason "
            "— a customer promised it, or sales has demoed it. Make them "
            "choose again. Accept a decision that names a trade-off; keep "
            "pressing on one that only names effort."
        ),
        probes=("prioritisation", "reasoning under constraint", "holding a decision"),
        resolve_when="they have made a call and defended it once under pressure",
        use_when="they mention deadlines, launches, or competing priorities",
    ),
)


def by_id(scenario_id: str) -> Scenario | None:
    return next((s for s in SCENARIOS if s.id == scenario_id), None)


def for_role(role: str) -> list[Scenario]:
    return [s for s in SCENARIOS if s.owner == role]


def catalogue(role: str) -> str:
    """The scenarios this persona may launch, as prompt text.

    Only its OWN are listed. A technical interviewer offered the customer's
    role-play will eventually launch it, and the panel stops being a panel the
    moment two personas can do the same job.
    """
    mine = for_role(role)
    if not mine:
        return ""

    lines = [
        "ROLE-PLAY. You may put the candidate INSIDE a situation instead of "
        "asking about one. Do this at most once, and only when their answer "
        "gives you a natural opening — never as your first question.",
        "",
        "Call launch_scenario with one of these ids, then deliver the setup "
        "line in your own words and stay in character:",
        "",
    ]
    for s in mine:
        lines.append(f"  {s.id} — {s.title}")
        lines.append(f"      use when: {s.use_when}")
    lines.append("")
    lines.append(
        "While a role-play is running you hold the floor and no other "
        "interviewer speaks. Call end_scenario the moment it resolves, then "
        "step out of character and continue the interview normally."
    )
    return "\n".join(lines)


def in_character(scenario: Scenario, exchanges: int) -> str:
    """Instructions injected while a scenario is running.

    Re-sent EVERY turn rather than once at launch. A model given a character
    once and then eleven turns of conversation drifts back into being an
    interviewer, and the role-play dissolves without anyone deciding to end it.
    """
    parts = [
        f"YOU ARE CURRENTLY IN A ROLE-PLAY: {scenario.title}.",
        f"Stay in character. {scenario.stance}",
        f"End it when {scenario.resolve_when}.",
        "Do not narrate the exercise or explain what you are doing. Speak as "
        "the character, in one short turn.",
    ]
    remaining = MAX_EXCHANGES - exchanges
    if remaining <= 1:
        parts.append(
            "This is the LAST exchange. Bring it to a close, call "
            "end_scenario, and step back out of character."
        )
    return "\n".join(parts)
