# 单元测试编写规则（强制契约）

> 本文件是 batch-unit-test-generator 技能中单测编写的**唯一权威来源**。
> 规则 c / d 为机器强校验：`validate_rules.py` 在 mvn 执行前逐条判定，
> 任何违规都会阻断流程，其输出是唯一判定依据，不得手工复核。
> 规则 a / b / e 为软约束，由提示词层约束，不做机器判定。
> 规则 f-1 为运行时强校验：verify_coverage.py / init_coverage.py 解析 surefire-reports，
> 出现 Failures/Errors 即判失败，与覆盖率数字无关。
> 规则 f-2（有效断言）为提示词层软约束，无机器校验。
> **覆盖率目标与规则冲突时，规则优先**：宁可触发升级协议（3 轮无提升询问用户），
> 也不允许违规达标。

技术栈：JUnit5（`org.junit.jupiter`）+ Mockito（`mockito-core` / `mockito-junit-jupiter`）。

---

## 规则 a：private 方法连带覆盖（软约束）

被测方法调用的本类 private 方法必须一并覆盖：优先通过公有入口间接覆盖；
覆盖率仍不足时，用 `ReflectionTestUtils` 直接调用。

```java
// ✓ 优先: 通过公有入口间接覆盖 private 辅助方法
@Test
void testProcess_validInput() {
    assertEquals("OK", service.process("in"));  // process 内部调用 private validate()
}

// ✓ 兜底: ReflectionTestUtils 直测 private 方法(模板)
@Test
void testValidate_empty() {
    Boolean result = ReflectionTestUtils.invokeMethod(service, "validate", "");
    assertFalse(result);
}

// ✓ private 字段注入
ReflectionTestUtils.setField(service, "maxRetry", 3);
```

## 规则 b：非本类调用一律 mock（软约束）

被测类依赖的一切非本类对象（Spring Bean、工具类、静态方法、外部服务）一律 mock，
不做真实调用，保证测试隔离与可重复。

```java
// ✓ Mockito 模板: 依赖对象全部 mock 注入
@ExtendWith(MockitoExtension.class)
class OrderServiceTest {
    @Mock
    private OrderMapper orderMapper;
    @InjectMocks
    private OrderService orderService;

    @Test
    void testGetOrder_exists() {
        when(orderMapper.selectById(1L)).thenReturn(new Order(1L, "paid"));
        assertEquals("paid", orderService.getOrder(1L).getStatus());
    }
}

// ✓ MockedStatic 模板: 静态方法 mock, @BeforeEach 打开 / @AfterEach 关闭
private MockedStatic<SpringUtil> springUtilMock;

@BeforeEach
void setUp() {
    springUtilMock = Mockito.mockStatic(SpringUtil.class);
}

@AfterEach
void tearDown() {
    springUtilMock.close();
}
```

### 禁止 Spring 容器测试

禁止 `@SpringBootTest`/`@WebMvcTest`/`@DataJpaTest` 等容器与切片注解、禁止真实 Bean 注入。
`@Autowired` 字段用 `@InjectMocks` 或 `ReflectionTestUtils.setField` 注入。

### Mockito strict stubs 指引

`@ExtendWith(MockitoExtension.class)` 默认 strict stubs，`when()` 未被走到即抛 `UnnecessaryStubbingException`——这是编写 Mockito 测试的高频翻车点，浪费迭代预算。

- 每个用例只 stub 实际执行到的调用；报 `UnnecessaryStubbing` 时优先删除未用 stub；
- 跨用例共用或不确定是否触发的 stub 用 `lenient()`；

```java
// ✗ 违规: stub 了未被调用的方法, strict stubs 下抛 UnnecessaryStubbingException
@Test
void testGetOrder_notExists() {
    when(orderMapper.selectById(1L)).thenReturn(new Order(1L, "paid"));  // 未被走到
    when(orderMapper.selectById(2L)).thenReturn(null);
    assertNull(orderService.getOrder(2L));
}

// ✓ 修正: 删除未用 stub
@Test
void testGetOrder_notExists() {
    when(orderMapper.selectById(2L)).thenReturn(null);
    assertNull(orderService.getOrder(2L));
}

// ✓ 或: 不确定是否触发时用 lenient() 标记单个 stub
lenient().when(orderMapper.selectById(1L)).thenReturn(new Order(1L, "paid"));
```

## 规则 c：禁止异常捕获（机器强校验，validate_rules.py 为唯一判定依据）

禁止任何形式的 `catch` 块与裸 `try { ... }` 块；**不带 catch 的
try-with-resources（`try (...)` 后无 catch 子句）放行**——资源由 `AutoCloseable`
自动关闭，不属于异常捕获。异常路径验证统一使用 `assertThrows`。

```java
// ✗ 违规: try-catch 捕获后断言
@Test
void testFoo_null() {
    try {
        service.foo(null);
        fail("应抛出异常");
    } catch (ServiceException e) {
        assertEquals("参数错误", e.getMessage());
    }
}

// ✗ 违规: try-with-resources 搭配 catch 同样禁止
try (Stream<String> s = open()) { ... } catch (IOException e) { ... }

// ✓ 正确: assertThrows 验证异常路径
@Test
void testFoo_null() {
    assertThrows(ServiceException.class, () -> service.foo(null));
}

// ✓ 正确: 不带 catch 的 try-with-resources 放行
@Test
void testBar() {
    try (MockedStatic<SpringUtil> mocked = mockStatic(SpringUtil.class)) {
        mocked.when(() -> SpringUtil.getBean(Foo.class)).thenReturn(foo);
        assertEquals("ok", service.bar());
    }
}
```

> 既要断言异常类型又要断言消息时，可用 `assertThrows` 返回值继续断言：
> `ServiceException ex = assertThrows(...); assertEquals("参数错误", ex.getMessage());`

## 规则 d：不允许修改非测试类（机器强校验，validate_rules.py 为唯一判定依据）

唯一允许写入的文件是目标测试类（`state.json` 的 `test_class_file`，位于对应模块
`src/test/java` 同包下）。`validate_rules.py` 以 `git status --porcelain` 对比
`state.json` 基线（init 时快照），`**/src/main/java/**` 路径下出现基线之外的变更即违规。

- 禁止以任何理由修改 `src/main/java` 下的业务代码、配置、pom.xml。
- 测试失败的根因在业务代码（可见性、硬编码依赖等）时，属"测试侧无法解决"，
  交由升级协议处理，绝不通过改业务代码让测试通过。

## 规则 e：测试类不存在时创建（软约束）

测试类路径为 `<模块>/src/test/java/<同包>/<类名>Test.java`；不存在时按下述骨架创建：

```java
package com.example.demo;   // 与被测类同包

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class FooServiceTest {

    @Mock
    private BarMapper barMapper;

    @InjectMocks
    private FooService fooService;

    @Test
    void agentMethod_scenario() {
        // 前置 mock...
        // 调用被测方法...
        // 方法以断言结尾
        assertEquals("expected", "actual");
    }
}
```

## 规则 f：测试必须真实通过

### f-1：测试必须真实通过（运行时强校验，surefire-reports 为唯一判定依据）

每轮 mvn 后由 verify_coverage.py / init_coverage.py 解析 `target/surefire-reports`：
只要出现 Failures 或 Errors，无论覆盖率数字如何，该轮一律不算达标
（不得标 done/finish）；仅当 Failures=0 且 Errors=0 时，才与覆盖率联合判定达标。

### f-2：有效断言（提示词层软约束，无机器校验）

每个 @Test 方法必须以断言结尾；禁止为凑覆盖率编写必然失败的断言或无断言空跑用例。
断言必须是独立于实现的预期值。

**三条作弊禁令**（机器盲区——rules.py 查不出、surefire 判不红）：

1. **禁止跳过消红**：不得用 `@Disabled`/`@DisabledIf`/`assumeTrue` 跳过失败用例换取绿灯
   （skipped 不计入 Failures/Errors，但不算真实通过）；
2. **禁止删除消红**：不得通过删除既有失败测试方法消除失败，只能修复；
   确属无法修复走升级协议；
3. **禁止同义反复断言**：禁止"抄答案"（先执行取实际返回值再把它同时当期望值与实际值）、
   `assertEquals(x, x)`、断言两个字面量相等。

```java
// ✗ 违规: 为凑覆盖率写必然失败的断言
@Test
void testFoo_coverageOnly() {
    service.foo("in");
    assertEquals("impossible", "value");  // 明知必失败, 凑行覆盖
}

// ✗ 违规: 无断言空跑用例(仅刷覆盖率)
@Test
void testFoo_noAssertion() {
    service.foo("in");
}

// ✗ 违规: 抄答案——先取实际值再当期望值
@Test
void testFoo_circularAssert() {
    String result = service.foo("in");
    assertEquals(result, result);  // 恒真, 不验证任何预期
}

// ✓ 正确: 断言反映真实预期, 测试可稳定通过
@Test
void testFoo_validInput() {
    assertEquals("ok", service.foo("in"));
}
```

## 附：覆盖率排除（配置项，非编号规则）

模式语义与状态流转见 SKILL.md §4「覆盖率排除」。对写测试的约束：

- 被排除的内部类不进入方法表，**不必也不得**为其编写测试；
- 排除模式由用户在命令行指定，编写测试时不得建议以排除方式规避
  规则 f（不得为凑覆盖率排除被测目标或其真实业务方法）；
- 若达标困难源于 Lombok 等生成代码，可经 ask_user 建议用户配置
  `lombok.config`（`lombok.addLombokGeneratedAnnotation = true`，JaCoCo 默认
  过滤 `lombok.Generated` 方法）或追加 `--coverage-exclude`，由用户决定。
