// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/base_devtool/native/public/cdp_param_utils.h"

#include <limits>
#include <string>
#include <utility>
#include <vector>

#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace testing {

TEST(CDPParamUtilsTest, ReadIntAcceptsSignedAndUnsignedValuesWithinIntRange) {
  const int values[] = {std::numeric_limits<int>::min(), -1, 0, 1,
                        std::numeric_limits<int>::max()};
  for (int expected : values) {
    int result = 42;
    EXPECT_TRUE(ReadIntParam(Json::Value(expected), result));
    EXPECT_EQ(result, expected);
    if (expected >= 0) {
      EXPECT_TRUE(
          ReadIntParam(Json::Value(static_cast<Json::UInt>(expected)), result));
      EXPECT_EQ(result, expected);
    }
  }
}

TEST(CDPParamUtilsTest, ReadIntRejectsOtherTypesWithoutChangingOutput) {
  const std::vector<Json::Value> values = {Json::Value(),
                                           Json::Value(true),
                                           Json::Value(false),
                                           Json::Value("1"),
                                           Json::Value(1.0),
                                           Json::Value(1.5),
                                           Json::Value(Json::arrayValue),
                                           Json::Value(Json::objectValue)};
  for (const auto& value : values) {
    SCOPED_TRACE(value.toStyledString());
    int result = 42;
    EXPECT_FALSE(ReadIntParam(value, result));
    EXPECT_EQ(result, 42);
  }
}

TEST(CDPParamUtilsTest, ReadIntRejectsOverflowWithoutChangingOutput) {
  const std::vector<Json::Value> values = {
      Json::Value(static_cast<Json::Int64>(std::numeric_limits<int>::min()) -
                  1),
      Json::Value(static_cast<Json::Int64>(std::numeric_limits<int>::max()) +
                  1),
      Json::Value(std::numeric_limits<Json::UInt64>::max()),
      Json::Value(std::numeric_limits<Json::Int64>::min())};
  for (const auto& value : values) {
    int result = 42;
    EXPECT_FALSE(ReadIntParam(value, result));
    EXPECT_EQ(result, 42);
  }
}

TEST(CDPParamUtilsTest, ReadIntRejectsNonFiniteNumbers) {
  const double values[] = {std::numeric_limits<double>::infinity(),
                           -std::numeric_limits<double>::infinity(),
                           std::numeric_limits<double>::quiet_NaN()};
  for (double value : values) {
    int result = 42;
    EXPECT_FALSE(ReadIntParam(Json::Value(value), result));
    EXPECT_EQ(result, 42);
  }
}

TEST(CDPParamUtilsTest, ReadIntStringAcceptsDecimalValuesWithinIntRange) {
  const int values[] = {std::numeric_limits<int>::min(), -1, 0, 1,
                        std::numeric_limits<int>::max()};
  for (int expected : values) {
    int result = 42;
    EXPECT_TRUE(
        ReadIntStringParam(Json::Value(std::to_string(expected)), result));
    EXPECT_EQ(result, expected);
  }
  int result = 42;
  EXPECT_TRUE(ReadIntStringParam(Json::Value("+001"), result));
  EXPECT_EQ(result, 1);
  EXPECT_TRUE(ReadIntStringParam(Json::Value("-0"), result));
  EXPECT_EQ(result, 0);
}

TEST(CDPParamUtilsTest, ReadIntStringRejectsMalformedStrings) {
  const std::vector<std::string> values = {"",
                                           "+",
                                           "-",
                                           " 1",
                                           "\t1",
                                           "1 ",
                                           "1suffix",
                                           "1.0",
                                           "1e2",
                                           "0x10",
                                           std::string("1\0suffix", 8),
                                           std::string("\xff", 1)};
  for (const auto& value : values) {
    int result = 42;
    EXPECT_FALSE(ReadIntStringParam(Json::Value(value), result));
    EXPECT_EQ(result, 42);
  }
}

TEST(CDPParamUtilsTest, ReadIntStringRejectsOverflow) {
  const std::vector<std::string> values = {
      std::to_string(static_cast<int64_t>(std::numeric_limits<int>::min()) - 1),
      std::to_string(static_cast<int64_t>(std::numeric_limits<int>::max()) + 1),
      "999999999999999999999999999999", "-999999999999999999999999999999"};
  for (const auto& value : values) {
    int result = 42;
    EXPECT_FALSE(ReadIntStringParam(Json::Value(value), result));
    EXPECT_EQ(result, 42);
  }
}

TEST(CDPParamUtilsTest, ReadIntStringRejectsNonStringTypes) {
  const std::vector<Json::Value> values = {Json::Value(),
                                           Json::Value(true),
                                           Json::Value(1),
                                           Json::Value(1.0),
                                           Json::Value(Json::arrayValue),
                                           Json::Value(Json::objectValue)};
  for (const auto& value : values) {
    int result = 42;
    EXPECT_FALSE(ReadIntStringParam(value, result));
    EXPECT_EQ(result, 42);
  }
}

TEST(CDPParamUtilsTest, ReadFiniteNumberAcceptsIntegerAndRealValues) {
  const std::vector<std::pair<Json::Value, double>> values = {
      {Json::Value(-1), -1.0},
      {Json::Value(0), 0.0},
      {Json::Value(static_cast<Json::UInt>(1)), 1.0},
      {Json::Value(0.5), 0.5},
      {Json::Value(std::numeric_limits<double>::max()),
       std::numeric_limits<double>::max()}};
  for (const auto& [value, expected] : values) {
    double result = 42.0;
    EXPECT_TRUE(ReadFiniteNumberParam(value, result));
    EXPECT_DOUBLE_EQ(result, expected);
  }
}

TEST(CDPParamUtilsTest, ReadFiniteNumberRejectsOtherTypesAndNonFiniteValues) {
  const std::vector<Json::Value> values = {
      Json::Value(),
      Json::Value(true),
      Json::Value("0.5"),
      Json::Value(Json::arrayValue),
      Json::Value(Json::objectValue),
      Json::Value(std::numeric_limits<double>::infinity()),
      Json::Value(-std::numeric_limits<double>::infinity()),
      Json::Value(std::numeric_limits<double>::quiet_NaN())};
  for (const auto& value : values) {
    double result = 42.0;
    EXPECT_FALSE(ReadFiniteNumberParam(value, result));
    EXPECT_DOUBLE_EQ(result, 42.0);
  }
}

TEST(CDPParamUtilsTest, ReadFiniteFloatAcceptsValuesWithinFloatRange) {
  const std::vector<std::pair<Json::Value, float>> values = {
      {Json::Value(-1), -1.0f},
      {Json::Value(0), 0.0f},
      {Json::Value(static_cast<Json::UInt>(1)), 1.0f},
      {Json::Value(0.5), 0.5f},
      {Json::Value(std::numeric_limits<float>::max()),
       std::numeric_limits<float>::max()},
      {Json::Value(-std::numeric_limits<float>::max()),
       -std::numeric_limits<float>::max()}};
  for (const auto& [value, expected] : values) {
    float result = 42.0f;
    EXPECT_TRUE(ReadFiniteFloatParam(value, result));
    EXPECT_FLOAT_EQ(result, expected);
  }
}

TEST(CDPParamUtilsTest, ReadFiniteFloatRejectsOtherTypesAndOutOfRangeValues) {
  const std::vector<Json::Value> values = {
      Json::Value(),
      Json::Value(true),
      Json::Value("0.5"),
      Json::Value(Json::arrayValue),
      Json::Value(Json::objectValue),
      Json::Value(std::numeric_limits<double>::infinity()),
      Json::Value(-std::numeric_limits<double>::infinity()),
      Json::Value(std::numeric_limits<double>::quiet_NaN()),
      Json::Value(std::numeric_limits<double>::max()),
      Json::Value(-std::numeric_limits<double>::max())};
  for (const auto& value : values) {
    float result = 42.0f;
    EXPECT_FALSE(ReadFiniteFloatParam(value, result));
    EXPECT_FLOAT_EQ(result, 42.0f);
  }
}

TEST(CDPParamUtilsTest, MissingFieldsAndNullParamsFailWithoutChangingOutput) {
  const Json::Value object(Json::objectValue);
  const Json::Value null;
  int result = 42;
  EXPECT_FALSE(ReadIntParam(object["nodeId"], result));
  EXPECT_FALSE(ReadIntParam(null["nodeId"], result));
  EXPECT_FALSE(ReadIntStringParam(object["layerId"], result));
  EXPECT_FALSE(ReadIntStringParam(null["layerId"], result));
  EXPECT_EQ(result, 42);
  double number_result = 42.0;
  EXPECT_FALSE(ReadFiniteNumberParam(object["a"], number_result));
  EXPECT_FALSE(ReadFiniteNumberParam(null["a"], number_result));
  EXPECT_DOUBLE_EQ(number_result, 42.0);
  float float_result = 42.0f;
  EXPECT_FALSE(ReadFiniteFloatParam(object["x"], float_result));
  EXPECT_FALSE(ReadFiniteFloatParam(null["x"], float_result));
  EXPECT_FLOAT_EQ(float_result, 42.0f);
}

TEST(CDPParamUtilsTest,
     GeneratedRulesEnforceRequiredFieldsEnumsAndNestedArrays) {
  const auto* rules = FindCDPParamRules("Input.emulateTouchFromMouseEvent");
  ASSERT_NE(rules, nullptr);
  Json::Value params;
  params["type"] = "mousePressed";
  params["button"] = "left";
  params["x"] = 1;
  std::string error;
  EXPECT_FALSE(ValidateCDPParams(params, *rules, error));
  params["y"] = 1;
  EXPECT_TRUE(ValidateCDPParams(params, *rules, error));
  params["type"] = "unknown";
  EXPECT_FALSE(ValidateCDPParams(params, *rules, error));
  rules = FindCDPParamRules("CSS.setStyleTexts");
  ASSERT_NE(rules, nullptr);
  params = Json::Value(Json::objectValue);
  params["edits"][0]["styleSheetId"] = "sheet";
  params["edits"][0]["text"] = "color: red";
  auto& range = params["edits"][0]["range"];
  range["startLine"] = range["endLine"] = 1;
  range["startColumn"] = range["endColumn"] = 0;
  EXPECT_TRUE(ValidateCDPParams(params, *rules, error));
  range["endLine"] = "1";
  EXPECT_FALSE(ValidateCDPParams(params, *rules, error));
  EXPECT_NE(error.find("params.edits[0].range.endLine"), std::string::npos);
  EXPECT_EQ(FindCDPParamRules("Input.typo"), nullptr);
}

TEST(CDPParamUtilsTest, SchemaSupportsUnionsMapsAndRecursiveReferences) {
  const CDPParamSchema value{CDPParamType::kString | CDPParamType::kNull};
  CDPParamSchema node{CDPParamType::kMap};
  const CDPParamProperty properties[] = {{"next", &node, true}};
  node.properties = properties;
  node.property_count = 1;
  node.values = &value;
  const CDPParamRuleSet rules{"Test.map", &node};
  std::string error;
  Json::Value params;
  params["first"] = Json::nullValue;
  params["next"]["dynamic"] = "text";
  EXPECT_TRUE(ValidateCDPParams(params, rules, error));
  params["next"]["dynamic"] = 1;
  EXPECT_FALSE(ValidateCDPParams(params, rules, error));
  EXPECT_NE(error.find("params.next.dynamic"), std::string::npos);
  Json::Value* nested = &params;
  params = Json::Value(Json::objectValue);
  for (int i = 0; i < 130; ++i) nested = &((*nested)["next"]);
  *nested = Json::Value(Json::objectValue);
  EXPECT_FALSE(ValidateCDPParams(params, rules, error));
  EXPECT_NE(error.find("maximum schema depth"), std::string::npos);
}

TEST(CDPParamUtilsTest, ContractNumbersAreIndependentOfNativeConversionLimits) {
  const CDPParamSchema integer{CDPParamType::kInteger};
  const CDPParamRuleSet rules{"Test.integer", &integer};
  std::string error;
  EXPECT_TRUE(
      ValidateCDPParams(Json::Value(Json::UInt64(1) << 40), rules, error));
  EXPECT_TRUE(ValidateCDPParams(Json::Value(1.0), rules, error));
  EXPECT_FALSE(ValidateCDPParams(Json::Value(1.5), rules, error));
  EXPECT_FALSE(ValidateCDPParams(Json::Value(true), rules, error));
  EXPECT_FALSE(ValidateCDPParams(
      Json::Value(std::numeric_limits<double>::infinity()), rules, error));
}

TEST(CDPParamUtilsTest, ValidateNestedArraysBeforeConversions) {
  const std::initializer_list<CDPParamRule> rules = {
      {"edits", CDPParamType::kArray},
      {"edits[]", CDPParamType::kObject},
      {"edits[].range", CDPParamType::kObject},
      {"edits[].range.line", CDPParamType::kInteger},
      {"edits[].text", CDPParamType::kString}};
  Json::Value params;
  params["edits"][0]["range"]["line"] = 1;
  params["edits"][0]["text"] = "color: red";
  std::string error;
  EXPECT_TRUE(ValidateCDPParams(params, rules, error));
  params["edits"][0]["range"]["line"] = "1";
  EXPECT_FALSE(ValidateCDPParams(params, rules, error));
  EXPECT_NE(error.find("params.edits[0].range.line"), std::string::npos);
  params["edits"][0] = 1;
  EXPECT_FALSE(ValidateCDPParams(params, rules, error));
  params["edits"] = Json::Value(Json::objectValue);
  EXPECT_FALSE(ValidateCDPParams(params, rules, error));
}

TEST(CDPParamUtilsTest, ValidateOptionalSubtreeAndRequiredFields) {
  const std::initializer_list<CDPParamRule> rules = {
      {"range", CDPParamType::kObject, true},
      {"range.line", CDPParamType::kInteger}};
  std::string error;
  EXPECT_TRUE(ValidateCDPParams(Json::Value(), rules, error));
  Json::Value params;
  params["range"] = Json::Value(Json::objectValue);
  EXPECT_FALSE(ValidateCDPParams(params, rules, error));
  params["range"]["line"] = 0;
  EXPECT_TRUE(ValidateCDPParams(params, rules, error));
  params["range"] = Json::nullValue;
  EXPECT_FALSE(ValidateCDPParams(params, rules, error));
  EXPECT_FALSE(ValidateCDPParams(Json::Value("1"), rules, error));
  EXPECT_FALSE(
      ValidateCDPParams(Json::Value(), {{"x", CDPParamType::kInteger}}, error));
}

TEST(CDPParamUtilsTest, ValidateNativeNumericLimitsAndUnknownFields) {
  Json::Value params;
  params["x"] = 1.5;
  params["extension"] = Json::Value(Json::arrayValue);
  std::string error;
  EXPECT_TRUE(
      ValidateCDPParams(params, {{"x", CDPParamType::kCoordinate}}, error));
  EXPECT_FALSE(
      ValidateCDPParams(params, {{"x", CDPParamType::kInteger}}, error));
  params["x"] = Json::UInt64(1) << 40;
  EXPECT_FALSE(
      ValidateCDPParams(params, {{"x", CDPParamType::kCoordinate}}, error));
  EXPECT_FALSE(
      ValidateCDPParams(params, {{"x", CDPParamType::kInteger}}, error));
  params["x"] = std::numeric_limits<double>::infinity();
  EXPECT_FALSE(
      ValidateCDPParams(params, {{"x", CDPParamType::kNumber}}, error));
  params["x"] = std::numeric_limits<double>::max();
  EXPECT_FALSE(ValidateCDPParams(params, {{"x", CDPParamType::kFloat}}, error));
}

}  // namespace testing
}  // namespace devtool
}  // namespace lynx
