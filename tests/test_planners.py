import unittest

from ch00chka.application.planners import UrlActionPlanner
from ch00chka.domain import ActionType, NormalizedMessage


class UrlActionPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_url_becomes_independent_media_action(self):
        message = NormalizedMessage(
            chat_id=1,
            message_id=1,
            user_id=1,
            user_name="Tester",
            text="links",
            chat_type="group",
            bot_name="Bot",
            urls=("https://example.test/one", "https://example.test/two"),
        )

        plan = await UrlActionPlanner().plan(message)

        self.assertEqual(len(plan.actions), 2)
        self.assertTrue(all(action.type is ActionType.MEDIA_DOWNLOAD for action in plan.actions))


if __name__ == "__main__":
    unittest.main()
