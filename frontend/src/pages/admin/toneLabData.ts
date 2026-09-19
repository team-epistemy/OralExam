// Ported verbatim from examiner-tone-lab_1.html (the standalone Stage-0 instrument).
// Sample fixtures — real stems + graphs arrive via "Fetch live cases"; the student
// scripts and watch_terms are eval artifacts authored here, not product data.
/* eslint-disable */

export interface WatchTerm { label: string; patterns: string[] }
export interface CaseEdge { id: string; eps: number; text: string }
export interface ScriptTurn { text: string; terms: string[] }
export interface CaseDef {
  domain: string;
  stem: string;
  graph: CaseEdge[];
  watch_terms: WatchTerm[];
  scripts: Record<string, ScriptTurn[]>;
}
export interface ArmDef { input_mode: "chat" | "block"; expects_json: boolean; label: string }

export const SHARED_PREAMBLE: string = "You are conducting a short oral examination. Your purpose is to find out how much\nof this question's causal structure the student can traverse on their own. You are\nnot teaching, not helping them arrive at the answer, and not assessing their\nconfidence. You are finding the edge of what they can reconstruct unaided.\n\nYou receive on every turn:\n  QUESTION         the exam prompt\n  CONTEXT_GRAPH    nodes with saliency sigma, edges with saliency epsilon\n  STUDENT_TERMS    every graph label the student has already produced, maintained\n                   by the system, including paraphrases it has resolved\n  TRANSCRIPT       the turns so far\n  TURNS_REMAINING  integer\n\nHARD CONSTRAINTS. These bind on every turn and override anything below.\n\nC1  Never state, name, define, gesture at, or hint at any node or edge label that\n    is not already in STUDENT_TERMS. This covers synonyms, near-paraphrases, and\n    ordinary-language restatements of the same idea. If you cannot ask about a\n    target without breaking C1, the target is wrong. Pick another.\nC2  Never evaluate correctness. Do not confirm, deny, or correct any specific\n    claim the student has made, including by implication. An error is recorded and\n    left standing.\nC3  Never ask a question that contains its own answer.\nC4  One conversational move per turn. Never stack two questions, and never join\n    them with \"and\" or \"also\".\nC5  Never explain, never summarize the student's reasoning back to them, never\n    restate their point in better words, never preview where the exam is going.\nC6  This is spoken aloud. Plain sentences only. No lists, no markdown, no\n    parentheticals, no symbols, no numerals where a word will do.\nC7  If the student asks you a content question, do not answer it. Return it to\n    them once, then continue.\n\nPROBE SELECTION. Identical in every condition.\n\nP1  Target the highest-epsilon edge not yet traversed whose source node is already\n    in STUDENT_TERMS.\nP2  If no edge qualifies, target the highest-sigma node adjacent to something in\n    STUDENT_TERMS.\nP3  Refer to the target only through words the student has already used, or\n    through ordinary non-technical language. Apply C1 to the wording before you\n    speak it. If the wording fails C1, move to the next candidate target.\nP4  If the transcript shows you have already probed a target twice and the student\n    has not taken it up, treat that target as exhausted and move to the next\n    candidate. Do not rephrase a third time.\n\nSCAFFOLD. Identical in every condition. Fires only on trigger.\n\nS1  Triggers: silence beyond the configured threshold, an explicit \"I don't know\",\n    or an explicit request for help.\nS2  Emit the smallest possible reframing of the same probe. Do not narrow it into\n    a yes/no. Do not supply any part of the target edge. C1 still binds.\nS3  Set scaffold_used true for that turn.\n\nOUTPUT. Return only this JSON object, no prose around it.\n\n{\"receipt\": \"\", \"probe\": \"\", \"scaffold_used\": false, \"target\": \"<edge or node id>\"}\n\nThe harness concatenates receipt and probe for speech. Leave receipt as an empty\nstring when your turn rules give you no receipt slot.\n";

export const TURN_RULES: Record<string, string> = {
  "A1": "TURN RULES\n\nYour entire turn is the probe. There is no receipt.\n\nBegin with the question itself. Do not open with okay, so, right, got it, I see,\ninteresting, alright, mm, thanks, or any other token that acknowledges the answer\nbefore the question. Do not transition, bridge, or signal a change of topic.\n\nMaximum twenty-five words. One question.\n\nreceipt must be the empty string.\n",
  "A2": "TURN RULES\n\nYour turn is a receipt followed by a probe.\n\nRECEIPT. Eight words at most. It signals that you heard, and nothing else. It may\nname the topic the student just addressed using their own words, at or below their\nlevel of specificity. It may not evaluate, praise, upgrade their vocabulary,\nrestate a mechanism, or add any content whatsoever.\n\n  Permitted shapes, written here without quotation marks because the receipt value\n  you return must contain no quotation marks of its own.\n    Okay.\n    Okay, on the <student's own word>.\n    Understood.\n    Let's stay with <student's own word>.\n    Mm.\n\n  Forbidden in the receipt, without exception\n    good, great, exactly, correct, right, nice, perfect, well done, interesting,\n    fair, strong, useful, that makes sense, I follow, and every other word or\n    phrase carrying approval or disapproval. Also forbidden: any noun the student\n    has not said, and any restatement of what they meant.\n\nPROBE. Twenty-five words at most. One question. Selected by P1 to P3.\n",
  "A3": "TURN RULES\n\nYour turn is a receipt followed by a probe.\n\nRECEIPT. Eight words at most. It is warm. It acknowledges the effort of the answer\nand keeps the student comfortable. It may express approval of their engagement.\n\n  Permitted shapes, written here without quotation marks because the receipt value\n  you return must contain no quotation marks of its own.\n    Good, thanks.\n    Nice, that's a solid start.\n    Great, I follow you.\n    Good, let's keep going.\n\n  Still forbidden, and C1 and C2 still bind\n    Confirming any specific claim as correct, whether stated or implied. Warmth\n    attaches to the attempt, never to the content. \"Exactly, because the rate\n    falls\" is a violation even though it is warm. So is any noun the student has\n    not said.\n\nPROBE. Twenty-five words at most. One question. Selected by P1 to P3.\n"
};

export const CASES: Record<string, CaseDef> = {
  "Q1": {
    "domain": "Pricing",
    "stem": "A SaaS company is switching from cost-plus to value-based pricing. Walk me through what would have to be true about their customers for that switch to raise profit.",
    "graph": [
      {
        "id": "E1",
        "eps": 0.9,
        "text": "heterogeneity in WTP -> segmented pricing captures surplus a single price leaves"
      },
      {
        "id": "E2",
        "eps": 0.85,
        "text": "WTP above cost floor -> headroom exists at all"
      },
      {
        "id": "E3",
        "eps": 0.8,
        "text": "value must be perceivable and communicable -> WTP is realised, not theoretical"
      },
      {
        "id": "E4",
        "eps": 0.75,
        "text": "price increase -> volume loss via elasticity -> net margin effect ambiguous"
      },
      {
        "id": "E5",
        "eps": 0.7,
        "text": "competitive reference price caps realisable WTP"
      }
    ],
    "watch_terms": [
      {
        "label": "willingness to pay",
        "patterns": [
          "willingness to pay",
          "\\bWTP\\b"
        ]
      },
      {
        "label": "perceived value",
        "patterns": [
          "perceived value",
          "value percei\\w+"
        ]
      },
      {
        "label": "cost-plus",
        "patterns": [
          "cost[- ]plus"
        ]
      },
      {
        "label": "margin",
        "patterns": [
          "\\bmargins?\\b"
        ]
      },
      {
        "label": "segmentation",
        "patterns": [
          "segment\\w*"
        ]
      },
      {
        "label": "competitive alternatives",
        "patterns": [
          "alternatives?\\b",
          "substitutes?\\b",
          "competitors?\\b"
        ]
      },
      {
        "label": "surplus",
        "patterns": [
          "\\bsurplus\\b"
        ]
      },
      {
        "label": "heterogeneity",
        "patterns": [
          "heterogene\\w+",
          "var\\w+ (across|between|from customer)",
          "differ\\w* (across|between) customers",
          "different customers",
          "customer base"
        ]
      },
      {
        "label": "cost floor",
        "patterns": [
          "cost floor",
          "price floor",
          "marginal cost",
          "unit cost"
        ]
      },
      {
        "label": "elasticity",
        "patterns": [
          "elastic\\w*",
          "demand curve"
        ]
      },
      {
        "label": "volume",
        "patterns": [
          "\\bvolumes?\\b",
          "\\bunits? sold\\b",
          "how many customers buy"
        ]
      },
      {
        "label": "churn",
        "patterns": [
          "\\bchurn\\w*\\b",
          "customers? leav\\w+",
          "cancel\\w*"
        ]
      },
      {
        "label": "reference price",
        "patterns": [
          "reference price",
          "anchor price"
        ]
      }
    ],
    "scripts": {
      "S": [
        {
          "text": "Value-based pricing is about aligning price with the value the customer perceives rather than your internal cost structure. It's the more strategic approach because it captures willingness to pay instead of anchoring on cost-plus margins.",
          "terms": [
            "perceived value",
            "cost-plus",
            "willingness to pay",
            "margin"
          ]
        },
        {
          "text": "So the key is really understanding the customer's value drivers. You segment the market, you understand willingness to pay in each segment, and you price accordingly. Cost-plus leaves money on the table.",
          "terms": [
            "segmentation"
          ]
        },
        {
          "text": "There's a competitive dimension as well. You have to be aware of the alternatives available and position your price against the value you deliver versus substitutes.",
          "terms": [
            "competitive alternatives"
          ]
        },
        {
          "text": "The main risk is execution. You need good data on customer value and a sales team that can actually communicate it, otherwise the model doesn't land.",
          "terms": []
        },
        {
          "text": "So overall it raises profit when you're capturing more of the surplus that was previously going uncaptured under cost-plus.",
          "terms": [
            "surplus"
          ]
        }
      ]
    }
  },
  "Q2": {
    "domain": "Accounting",
    "stem": "A company reports rising net income and falling operating cash flow for three straight quarters. What could be going on?",
    "graph": [
      {
        "id": "E1",
        "eps": 0.95,
        "text": "revenue recognised on delivery not collection -> receivables rise -> working capital consumes cash"
      },
      {
        "id": "E2",
        "eps": 0.85,
        "text": "receivables growth appears as a negative adjustment in the CFO reconciliation"
      },
      {
        "id": "E3",
        "eps": 0.8,
        "text": "inventory built ahead of demand -> cash out now, no income effect until sold through COGS"
      },
      {
        "id": "E4",
        "eps": 0.75,
        "text": "capitalising costs -> lower expense and higher NI, cash still spent, lands in investing not operating"
      },
      {
        "id": "E5",
        "eps": 0.6,
        "text": "stretching payables would improve CFO, so a reversal of that worsens it"
      }
    ],
    "watch_terms": [
      {
        "label": "accrual",
        "patterns": [
          "accrual\\w*",
          "accrued"
        ]
      },
      {
        "label": "revenue recognition",
        "patterns": [
          "revenue recogni\\w+",
          "recognis\\w+ revenue",
          "recogniz\\w+ revenue"
        ]
      },
      {
        "label": "receivables",
        "patterns": [
          "receivables?\\b",
          "\\bAR\\b",
          "amounts owed"
        ]
      },
      {
        "label": "inventory",
        "patterns": [
          "inventor\\w+",
          "\\bstock on hand\\b"
        ]
      },
      {
        "label": "payables",
        "patterns": [
          "payables?\\b",
          "\\bAP\\b"
        ]
      },
      {
        "label": "working capital",
        "patterns": [
          "working capital"
        ]
      },
      {
        "label": "quality of earnings",
        "patterns": [
          "quality of earnings",
          "aggressive\\w* recogni\\w+"
        ]
      },
      {
        "label": "depreciation",
        "patterns": [
          "depreciat\\w+",
          "amortis\\w+",
          "amortiz\\w+",
          "non[- ]cash charges?"
        ]
      },
      {
        "label": "reconciliation",
        "patterns": [
          "reconcil\\w+",
          "add[- ]backs?"
        ]
      },
      {
        "label": "days sales outstanding",
        "patterns": [
          "days sales outstanding",
          "\\bDSO\\b",
          "collection period"
        ]
      },
      {
        "label": "capitalisation",
        "patterns": [
          "capitalis\\w+",
          "capitaliz\\w+"
        ]
      },
      {
        "label": "cost of goods sold",
        "patterns": [
          "cost of goods sold",
          "\\bCOGS\\b"
        ]
      },
      {
        "label": "channel stuffing",
        "patterns": [
          "channel stuff\\w+"
        ]
      },
      {
        "label": "investing activities",
        "patterns": [
          "investing activit\\w+",
          "investing section"
        ]
      },
      {
        "label": "deferred revenue",
        "patterns": [
          "deferred revenue",
          "unearned revenue"
        ]
      }
    ],
    "scripts": {
      "S": [
        {
          "text": "This is the classic accrual versus cash divergence. Under accrual accounting net income recognises revenue when it's earned rather than when cash moves, so the two can separate.",
          "terms": [
            "accrual",
            "revenue recognition"
          ]
        },
        {
          "text": "I'd want to look at the working capital accounts. Receivables, inventory, payables. Those are the usual suspects for a divergence like this.",
          "terms": [
            "working capital",
            "receivables",
            "inventory",
            "payables"
          ]
        },
        {
          "text": "Three straight quarters is a pattern rather than noise, so there's a quality of earnings question here. I'd be asking whether the revenue recognition is aggressive.",
          "terms": [
            "quality of earnings"
          ]
        },
        {
          "text": "Depreciation and the other non-cash charges are part of the reconciliation too, so the structure of the add-backs matters.",
          "terms": [
            "depreciation",
            "reconciliation"
          ]
        },
        {
          "text": "And cash flow is harder to manipulate than earnings, which is why analysts watch the divergence in the first place.",
          "terms": []
        }
      ]
    }
  },
  "Q3": {
    "domain": "Operations",
    "stem": "A clinic added a second check-in desk and the average patient wait fell much further than anyone expected. Why might that be?",
    "graph": [
      {
        "id": "E1",
        "eps": 0.95,
        "text": "wait is convex in utilisation -> added capacity near saturation helps disproportionately"
      },
      {
        "id": "E2",
        "eps": 0.85,
        "text": "pooling two queues cuts variability-driven wait beyond the capacity effect"
      },
      {
        "id": "E3",
        "eps": 0.8,
        "text": "arrival and service variability are what create waiting below full utilisation at all"
      },
      {
        "id": "E4",
        "eps": 0.6,
        "text": "Little's Law links queue length, arrival rate and flow time"
      }
    ],
    "watch_terms": [
      {
        "label": "capacity",
        "patterns": [
          "capacit\\w+"
        ]
      },
      {
        "label": "bottleneck",
        "patterns": [
          "bottleneck\\w*"
        ]
      },
      {
        "label": "queue",
        "patterns": [
          "queue\\w*",
          "\\bline\\b",
          "\\blines\\b"
        ]
      },
      {
        "label": "utilisation",
        "patterns": [
          "utilisation",
          "utilization"
        ]
      },
      {
        "label": "variability",
        "patterns": [
          "variabilit\\w+",
          "variance",
          "\\bvariable\\b",
          "randomness"
        ]
      },
      {
        "label": "congestion",
        "patterns": [
          "congest\\w+"
        ]
      },
      {
        "label": "Little's Law",
        "patterns": [
          "little'?s law"
        ]
      },
      {
        "label": "throughput",
        "patterns": [
          "throughput",
          "work in process",
          "\\bWIP\\b",
          "flow time"
        ]
      },
      {
        "label": "convexity",
        "patterns": [
          "convex\\w*",
          "non[- ]?linear\\w*",
          "\\bsteep\\w*\\b",
          "exponential\\w*",
          "disproportionate\\w*"
        ]
      },
      {
        "label": "saturation",
        "patterns": [
          "saturat\\w+",
          "\\bflat out\\b",
          "near capacity",
          "close to capacity",
          "maxed out"
        ]
      },
      {
        "label": "pooling",
        "patterns": [
          "pool\\w+",
          "single line",
          "one line",
          "shared queue",
          "combined queue"
        ]
      },
      {
        "label": "slack",
        "patterns": [
          "\\bslack\\b",
          "idle time",
          "breathing room"
        ]
      }
    ],
    "scripts": {
      "S": [
        {
          "text": "So this is a capacity question. They added capacity at the bottleneck and the queue cleared faster than a linear intuition would suggest.",
          "terms": [
            "capacity",
            "bottleneck",
            "queue"
          ]
        },
        {
          "text": "Queueing theory is the right lens. Utilisation is the key variable, and the relationship between utilisation and wait is not a simple one.",
          "terms": [
            "utilisation"
          ]
        },
        {
          "text": "Variability matters as well. Arrival variability and service time variability both drive congestion, and this system was probably quite variable.",
          "terms": [
            "variability",
            "congestion"
          ]
        },
        {
          "text": "There's Little's Law sitting in the background too, relating throughput, work in process and flow time.",
          "terms": [
            "Little's Law",
            "throughput"
          ]
        },
        {
          "text": "So the short answer is queueing dynamics rather than a simple capacity story.",
          "terms": []
        }
      ],
      "G": [
        {
          "text": "Before the second desk I'd guess the one desk was running almost flat out. When you're that close, the line doesn't clear between arrivals, so every burst of patients stacks on top of the last one instead of being absorbed.",
          "terms": [
            "queue",
            "saturation",
            "utilisation"
          ]
        },
        {
          "text": "Adding a desk drops how busy each person is, and the wait doesn't fall in proportion, it falls much faster, because that stacking goes away once there's slack between arrivals.",
          "terms": [
            "convexity",
            "slack"
          ]
        },
        {
          "text": "The other thing is that one line feeding two desks copes with a slow patient better than two separate lines would, because everyone else keeps moving past the holdup.",
          "terms": [
            "pooling",
            "variability"
          ]
        }
      ]
    }
  }
};

export const EVALUATIVE: string[] = ["good", "great", "exactly", "correct", "right", "nice", "perfect", "well done", "interesting", "fair point", "strong", "useful", "that makes sense", "i follow", "spot on", "absolutely", "excellent", "brilliant", "solid", "clear", "helpful"];
export const NEUTRAL_RECEIPT: string[] = ["okay", "ok", "understood", "mm", "right then", "let's stay", "lets stay", "noted", "thank you", "thanks", "i see", "got it", "alright", "so"];
export const CONFIRM_PATTERNS: string[] = ["\\b(yes|exactly|correct|that'?s right|precisely|indeed)\\b", "\\bthat'?s (it|correct|true)\\b", "\\byou'?re right\\b"];
export const COMPOUND_PATTERNS: string[] = ["\\band (what|how|why|does|do|is|are|would|could|should|did|can)\\b", "\\bor (what|how|why|does|do|is|are|would|could|should|did|can)\\b", "\\balso,? (what|how|why|tell|say|walk)\\b"];

export const ARMS: Record<string, ArmDef> = {
  "A0": {
    "input_mode": "chat",
    "expects_json": false,
    "label": "production prompt"
  },
  "A1": {
    "input_mode": "block",
    "expects_json": true,
    "label": "terse, probe only"
  },
  "A2": {
    "input_mode": "block",
    "expects_json": true,
    "label": "terse, neutral receipt"
  },
  "A3": {
    "input_mode": "block",
    "expects_json": true,
    "label": "terse, warm receipt"
  }
};
