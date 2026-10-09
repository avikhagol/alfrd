import test from "node:test";
import assert from "node:assert/strict";
import { fileToken, replaceFileToken } from "../../src/alfrd/web/js/components/file_autocomplete.js";

test("completes only the token containing the caret and normalizes separators", () => {
  const value = "first.fits, be\ntail.fits";
  assert.equal(fileToken(value, 14).query, "be");
  assert.deepEqual(replaceFileToken(value, 14, "beta.FITS"), { value: "first.fits,beta.FITS,tail.fits", cursor: 20 });
  assert.deepEqual(replaceFileToken("al", 2, "alpha.idifits"), { value: "alpha.idifits", cursor: 13 });
  assert.equal(replaceFileToken("a.fits,", 7, "b.fits").value, "a.fits,b.fits");
});
