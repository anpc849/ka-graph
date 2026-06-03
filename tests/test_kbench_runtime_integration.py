import kaggle_benchmarks as kbench

from kagraph import END, START, StateGraph


def test_kagraph_can_run_inside_kbench_task():
    graph = StateGraph()
    graph.add_node("answer", lambda state: {"answer": "ok"})
    graph.add_edge(START, "answer")
    graph.add_edge("answer", END)
    compiled = graph.compile()

    @kbench.task(name="kagraph_test_task", store_task=False, store_run=False)
    def evaluate(llm, question: str):
        result = compiled.invoke(question)
        kbench.assertions.assert_contains_regex("ok", result["answer"])
        return result["answer"]

    assert evaluate(kbench.llm, "hello") == "ok"
