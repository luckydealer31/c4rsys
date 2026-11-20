import { describe, expect, it } from "vitest";

import { positionOf } from "./namePosition";

describe("the position of a part read from its name", () => {
  it("reads Russian adjectives in any ending", () => {
    expect(positionOf("Фара правая mazda 3")).toMatchObject({ side: "right", longitudinal: "" });
    expect(positionOf("рычаг передний нижний левый")).toEqual({
      side: "left",
      longitudinal: "front",
      vertical: "lower",
      placement: "",
    });
    expect(positionOf("ручка двери наружная задней правой")).toMatchObject({
      side: "right",
      longitudinal: "rear",
      placement: "outer",
    });
  });

  it("reads English words and both words of a pair", () => {
    expect(positionOf("Door handle inner front RH")).toMatchObject({
      side: "right",
      longitudinal: "front",
      placement: "inner",
    });
    expect(positionOf("колодки передние и задние")).toMatchObject({ longitudinal: "both" });
  });

  it("does not take a part of another word", () => {
    expect(positionOf("правка кузова, лев, upright, frontier")).toEqual({
      side: "",
      longitudinal: "",
      vertical: "",
      placement: "",
    });
  });
});
