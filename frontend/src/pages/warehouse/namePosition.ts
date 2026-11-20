/** The position of a spare part read from its name (customer's decision of 20.11.2026): «фара
 * правая передняя» presses «Правая» and «Переднее». Russian adjectives in any ending and English
 * words; both words of a pair in the name mean «both». */

export type PositionKey = "side" | "longitudinal" | "vertical" | "placement";

const ENDING = "(?:ая|яя|ый|ий|ой|ое|ее|ые|ие|ого|его|ую|юю|ых|их|ым|им|ом|ем|ей|ому|ему|ыми|ими)";
const ru = (...stems: string[]) => `(?:${stems.join("|")})${ENDING}`;
// [key, first value, its words, second value, its words]
const WORDS: [PositionKey, string, string[], string, string[]][] = [
  ["side", "left", [ru("лев"), "left", "lh"], "right", [ru("прав"), "right", "rh"]],
  ["longitudinal", "front", [ru("передн"), "front"], "rear", [ru("задн"), "rear"]],
  ["vertical", "upper", [ru("верхн"), "upper"], "lower", [ru("нижн"), "lower"]],
  ["placement", "inner", [ru("внутренн"), "inner"], "outer", [ru("наружн", "внешн"), "outer"]],
];

const named = (text: string, words: string[]) =>
  new RegExp(`(?<![\\p{L}\\p{N}])(?:${words.join("|")})(?![\\p{L}\\p{N}])`, "u").test(text);

export function positionOf(name: string): Record<PositionKey, string> {
  const text = name.toLowerCase();
  const result = { side: "", longitudinal: "", vertical: "", placement: "" };
  for (const [key, a, wordsA, b, wordsB] of WORDS) {
    const first = named(text, wordsA);
    const second = named(text, wordsB);
    result[key] = first && second ? "both" : first ? a : second ? b : "";
  }
  return result;
}
