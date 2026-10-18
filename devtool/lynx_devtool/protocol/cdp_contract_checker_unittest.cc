// Copyright 2026 The Lynx Authors. All rights reserved.
// Licensed under the Apache License Version 2.0 that can be found in the
// LICENSE file in the root directory of this source tree.

#include "devtool/lynx_devtool/protocol/cdp_contract_checker.h"

#include <memory>
#include <string>
#include <vector>

#include "devtool/base_devtool/native/public/cdp_traffic_observer.h"
#include "third_party/googletest/googletest/include/gtest/gtest.h"

namespace lynx {
namespace devtool {
namespace testing {

namespace {

using Kind = CDPContractViolation::Kind;

// A hand-written contract in the compact form emitted by
// `tools/cdpc write`, covering each schema feature once.
constexpr char kSchema[] = R"({
  "types": {
    "Board.Node": {"k": ["object"], "p": {
      "id": {"s": {"k": ["integer"]}},
      "child": {"s": {"r": "Board.Node"}, "o": 1}}}
  },
  "commands": {
    "Board.set": {
      "p": {"k": ["object"], "p": {
        "key": {"s": {"k": ["string"]}},
        "mode": {"s": {"k": ["string"], "e": ["a", "b"]}, "o": 1}}},
      "r": {"k": ["object"], "p": {}}},
    "Board.get": {
      "p": {"k": ["object"], "p": {}},
      "r": {"k": ["object"], "p": {
        "entries": {"s": {"k": ["array"], "i": {"k": ["object"], "p": {
          "key": {"s": {"k": ["string"]}}}}}},
        "size": {"s": {"k": ["number"]}},
        "counts": {"s": {"k": ["object"], "a": {"k": ["integer"]}}, "o": 1},
        "extra": {"s": {"k": ["any"]}, "o": 1},
        "node": {"s": {"r": "Board.Node"}, "o": 1}}}},
    "Board.version": {
      "p": {"k": ["object"], "p": {}},
      "r": {"k": ["string"]}},
    "Board.raw": {
      "p": {"k": ["object"]}, "po": 1,
      "r": {"k": ["object"], "p": {}}}
  },
  "events": {
    "Board.changed": {"p": {"k": ["object"], "p": {
      "key": {"s": {"k": ["string"]}}}}},
    "Board.cleared": {"p": {"k": ["object"], "p": {}}}
  }
})";

Json::Value Parse(const std::string& text) {
  Json::Value value;
  Json::Reader reader;
  EXPECT_TRUE(reader.parse(text, value, false)) << text;
  return value;
}

}  // namespace

class CDPContractCheckerTest : public ::testing::Test {
 protected:
  void SetUp() override {
    checker_ = CDPContractChecker::Create(
        kSchema, [this](const CDPContractViolation& violation) {
          violations_.push_back(violation);
        });
    ASSERT_NE(checker_, nullptr);
  }

  void Request(const std::string& text, const void* channel = kChannel) {
    checker_->OnIncomingMessage(channel, Parse(text));
  }

  void Send(const std::string& text, const void* channel = kChannel) {
    checker_->OnOutgoingMessage(channel, Parse(text));
  }

  std::string Problems() const {
    std::string all;
    for (const auto& violation : violations_) {
      all += std::string(CDPContractViolationKindName(violation.kind)) + " " +
             violation.method + ":";
      for (const auto& problem : violation.problems) {
        all += " [" + problem + "]";
      }
      all += "\n";
    }
    return all;
  }

  static constexpr const void* kChannel = &kSchema;
  std::shared_ptr<CDPContractChecker> checker_;
  std::vector<CDPContractViolation> violations_;
};

TEST_F(CDPContractCheckerTest, ValidTrafficHasNoViolations) {
  Request(R"({"id":1,"method":"Board.set","params":{"key":"k","mode":"a"}})");
  Send(R"({"id":1,"result":{}})");
  Request(R"({"id":2,"method":"Board.get"})");
  Send(R"({"id":2,"result":{"entries":[{"key":"k"}],"size":3,
           "counts":{"a":1},"extra":[1,"x"],
           "node":{"id":1,"child":{"id":2}}}})");
  Request(R"({"id":3,"method":"Board.version"})");
  Send(R"({"id":3,"result":"1.0"})");
  Request(R"({"id":4,"method":"Board.raw"})");
  Send(R"({"id":4,"error":{"code":-32000,"message":"failed"}})");
  Send(R"({"method":"Board.changed","params":{"key":"k"}})");
  Send(R"({"method":"Board.cleared"})");
  EXPECT_TRUE(violations_.empty()) << Problems();
}

TEST_F(CDPContractCheckerTest, ReportsInvalidRequestButToleratesUnknownFields) {
  Request(R"({"id":1,"method":"Board.set","params":{"mode":"c","x":1}})");
  ASSERT_EQ(violations_.size(), 1u) << Problems();
  EXPECT_EQ(violations_[0].kind, Kind::kInvalidRequest);
  EXPECT_EQ(violations_[0].method, "Board.set");
  EXPECT_EQ(
      violations_[0].problems,
      (std::vector<std::string>{"params.key: missing required field",
                                "params.mode: 'c' is not an allowed value"}));
}

TEST_F(CDPContractCheckerTest, ReportsResultShapeMismatches) {
  Request(R"({"id":7,"method":"Board.get"})");
  Send(R"({"id":7,"result":{"size":1.5,"entries":[{"key":3}],"counts":{"a":"x"},
           "node":{"id":1,"child":{}},"note":"hi"}})");
  ASSERT_EQ(violations_.size(), 1u) << Problems();
  EXPECT_EQ(violations_[0].kind, Kind::kInvalidResult);
  EXPECT_EQ(violations_[0].problems,
            (std::vector<std::string>{
                "result.counts.a: expected integer, got string",
                "result.entries[0].key: expected string, got integer",
                "result.node.child.id: missing required field",
                "result.note: undeclared field"}));
}

TEST_F(CDPContractCheckerTest, ReportsEmptySuccessForRequiredResultFields) {
  Request(R"({"id":8,"method":"Board.get"})");
  Send(R"({"id":8,"result":{}})");
  ASSERT_EQ(violations_.size(), 1u) << Problems();
  EXPECT_EQ(violations_[0].problems,
            (std::vector<std::string>{"result.entries: missing required field",
                                      "result.size: missing required field"}));
}

TEST_F(CDPContractCheckerTest, ReportsMalformedErrorAndEnvelope) {
  Request(R"({"id":1,"method":"Board.set","params":{"key":"k"}})");
  Send(R"({"id":1,"error":{"code":"x"}})");
  Request(R"({"id":2,"method":"Board.set","params":{"key":"k"}})");
  Send(R"({"id":2})");
  ASSERT_EQ(violations_.size(), 2u) << Problems();
  EXPECT_EQ(violations_[0].kind, Kind::kInvalidError);
  EXPECT_EQ(violations_[0].problems,
            (std::vector<std::string>{"error.code: expected integer",
                                      "error.message: expected string"}));
  EXPECT_EQ(violations_[1].kind, Kind::kInvalidResult);
}

TEST_F(CDPContractCheckerTest, ReportsSecondResponseAndKeepsChannelsApart) {
  int other_channel = 0;
  Request(R"({"id":1,"method":"Board.set","params":{"key":"k"}})");
  Send(R"({"id":1,"result":{}})", &other_channel);
  Send(R"({"id":1,"result":{}})");
  Send(R"({"id":1,"result":{}})");
  Send(R"({"id":null,"error":{"code":-32600,"message":"bad"}})");
  ASSERT_EQ(violations_.size(), 2u) << Problems();
  EXPECT_EQ(violations_[0].kind, Kind::kUnmatchedResponse);
  EXPECT_EQ(violations_[1].kind, Kind::kUnmatchedResponse);
}

TEST_F(CDPContractCheckerTest, ReportsEventMismatchesAndUndeclaredEvents) {
  Send(R"({"method":"Board.changed","params":{"key":1}})");
  Send(R"({"method":"Board.unknown"})");
  ASSERT_EQ(violations_.size(), 2u) << Problems();
  EXPECT_EQ(violations_[0].kind, Kind::kInvalidEvent);
  EXPECT_EQ(
      violations_[0].problems,
      (std::vector<std::string>{"params.key: expected string, got integer"}));
  EXPECT_EQ(violations_[1].kind, Kind::kUndeclaredEvent);
  EXPECT_EQ(violations_[1].method, "Board.unknown");
}

TEST_F(CDPContractCheckerTest, UncontractedMethodsAreOnlyCorrelated) {
  Request(R"({"id":1,"method":"Other.call","params":[1]})");
  Send(R"({"id":1,"result":{"anything":true}})");
  EXPECT_TRUE(violations_.empty()) << Problems();
}

TEST_F(CDPContractCheckerTest, RejectsInvalidSchema) {
  EXPECT_EQ(CDPContractChecker::Create("{", nullptr), nullptr);
  EXPECT_EQ(CDPContractChecker::Create(
                R"({"commands":{"A.b":{"p":{"k":["nope"]}}}})", nullptr),
            nullptr);
}

TEST_F(CDPContractCheckerTest, TrafficObserverRoutesOnlyCDPMessages) {
  SetCDPTrafficObserver(checker_);
  NotifyCDPIncomingMessage(kChannel, "CDP", R"({"id":5,"method":"Board.get"})");
  NotifyCDPOutgoingMessage(kChannel, "Other", std::string("not json"));
  NotifyCDPOutgoingMessage(kChannel, "CDP", Parse(R"({"id":5,"result":{}})"));
  SetCDPTrafficObserver(nullptr);
  NotifyCDPOutgoingMessage(kChannel, "CDP", std::string(R"({"id":9})"));
  ASSERT_EQ(violations_.size(), 1u) << Problems();
  EXPECT_EQ(violations_[0].method, "Board.get");
}

TEST(CDPGeneratedContractTest, GeneratedContractCoversLynxEntries) {
  std::vector<CDPContractViolation> violations;
  auto checker = CDPContractChecker::CreateWithGeneratedContract(
      [&violations](const CDPContractViolation& violation) {
        violations.push_back(violation);
      });
  ASSERT_NE(checker, nullptr);
  const void* channel = &violations;

  checker->OnIncomingMessage(channel,
                             Parse(R"({"id":1,"method":"Lynx.getVersion"})"));
  checker->OnOutgoingMessage(channel, Parse(R"({"id":1,"result":"3.4.0"})"));
  checker->OnOutgoingMessage(channel,
                             Parse(R"({"method":"WhiteBoard.onSharedDataAdded",
                         "params":{"key":"k","value":"1"}})"));
  checker->OnIncomingMessage(channel,
                             Parse(R"({"id":2,"method":"DOM.getDocument"})"));
  checker->OnOutgoingMessage(channel,
                             Parse(R"({"id":2,"result":{"root":{"nodeId":1,
          "backendNodeId":1,"nodeType":9,"nodeName":"#document",
          "localName":"","nodeValue":""}}})"));
  EXPECT_TRUE(violations.empty());

  checker->OnIncomingMessage(
      channel, Parse(R"({"id":3,"method":"WhiteBoard.getSharedData"})"));
  checker->OnOutgoingMessage(channel, Parse(R"({"id":3,"result":{}})"));
  ASSERT_EQ(violations.size(), 1u);
  EXPECT_EQ(violations[0].method, "WhiteBoard.getSharedData");
  EXPECT_EQ(
      violations[0].problems,
      (std::vector<std::string>{"result.entries: missing required field"}));
}

TEST_F(CDPContractCheckerTest, IgnoresIdsOutsideDispatcherRange) {
  // JsonCpp considers UINT64_MAX integral, but it cannot be converted to the
  // dispatcher's signed request id. Observing it must not throw or abort.
  Request(R"({"id":18446744073709551615,"method":"Board.get"})");
  Send(R"({"id":18446744073709551615,"result":{}})");
  Request(R"({"id":9223372036854775807,"method":"Board.get"})");
  Send(R"({"id":9223372036854775807,"result":{"entries":[],"size":0}})");
  EXPECT_TRUE(violations_.empty()) << Problems();
}

TEST(CDPGeneratedContractTest, ReferencedEventsHaveValidatedContracts) {
  std::vector<CDPContractViolation> violations;
  auto checker = CDPContractChecker::CreateWithGeneratedContract(
      [&violations](const CDPContractViolation& violation) {
        violations.push_back(violation);
      });
  ASSERT_NE(checker, nullptr);
  const void* channel = &violations;
  const char* events[] = {
      R"({"method":"Component.uselessUpdate","params":{"componentId":"comp-1"}})",
      R"({"method":"Component.uselessUpdate","params":null})",
      R"({"method":"DOM.inlineStyleUpdatesFlushed","params":{"timestamp":1.5,
          "nodeUpdates":[{"backendNodeId":26,"properties":[
            {"name":"width","value":"12.5"},{"name":"opacity","removed":true}]}]}})",
      R"({"method":"GlobalProps.changed","params":{"timestamp":42,
          "changes":[{"operation":"replace"}]}})",
      R"({"method":"LynxNativeModule.recordAdded","params":{"record":{
          "sequence":1,"phase":"invoke","args":[1,null,{"custom":true}]}}})",
      R"({"method":"Recording.recordingComplete","params":{"stream":["1"],
          "filenames":["/tmp/1001.json"],"sessionIDs":[1001],"recordFormat":"json"}})",
      R"({"method":"Recording.recordingComplete","params":{"stream":null,
          "filenames":null,"sessionIDs":null,"recordFormat":"json"}})",
      R"({"method":"Replay.end","params":{"stream":"1"}})",
  };
  for (const char* event : events) {
    checker->OnOutgoingMessage(channel, Parse(event));
    ASSERT_TRUE(violations.empty()) << event;
  }

  const char* invalid_events[] = {
      R"({"method":"Component.uselessUpdate","params":[]})",
      R"({"method":"DOM.inlineStyleUpdatesFlushed","params":{"timestamp":1,
          "nodeUpdates":[{"backendNodeId":26,"properties":[
            {"name":"width","value":12.5}]}]}})",
      R"({"method":"GlobalProps.changed","params":{"timestamp":42,
          "changes":[{"operation":"merge"}]}})",
      R"({"method":"LynxNativeModule.recordAdded","params":{"record":{"sequence":"1"}}})",
      R"({"method":"Recording.recordingComplete","params":{"stream":[1],
          "filenames":[],"sessionIDs":[],"recordFormat":"json"}})",
      R"({"method":"Replay.end","params":{}})",
  };
  for (const char* event : invalid_events) {
    violations.clear();
    checker->OnOutgoingMessage(channel, Parse(event));
    ASSERT_EQ(violations.size(), 1u) << event;
    EXPECT_EQ(violations[0].kind, Kind::kInvalidEvent) << event;
  }
}

}  // namespace testing
}  // namespace devtool
}  // namespace lynx
