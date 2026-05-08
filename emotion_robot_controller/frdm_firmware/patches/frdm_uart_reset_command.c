/*
 * FRDM patch: UART command to reboot the MCX-N panel.
 *
 * Jetson Dashboard sends:
 *
 *   Reset 0 0
 *
 * Add this command to your GUI Guider / serial monitor command table:
 *
 *   { "Reset", "<ignored>", "software reset FRDM", ResetGui },
 *
 * This is a soft reset. It only works if the UART receiver and command parser
 * are still running. If the MCU is hard-faulted or interrupts are dead, use the
 * physical RESET button, SWD/debug-probe reset, or wire Jetson GPIO to RESET_b.
 */

#include "fsl_debug_console.h"
#include "fsl_common.h"

void ResetGui(char *pValue)
{
    (void)pValue;

    PRINTF("ResetGui: software reset requested\r\n");

    /*
     * Give the UART/console a short moment to flush before reset. If your SDK
     * project does not use SDK_DelayAtLeastUs(), replace this with vTaskDelay()
     * or your board delay function.
     */
    SDK_DelayAtLeastUs(100000U, CLOCK_GetFreq(kCLOCK_CoreSysClk));

    __disable_irq();
    NVIC_SystemReset();
}

/*
 * If you are using the portable packet parser in
 * emotion_robot_controller/frdm_firmware/main.c instead of the GUI Guider
 * command table, change COMMAND_TYPE_RESET from "face/motion neutral" to:
 *
 *   case COMMAND_TYPE_RESET:
 *       send_ack(cmd->seq);
 *       SDK_DelayAtLeastUs(100000U, CLOCK_GetFreq(kCLOCK_CoreSysClk));
 *       __disable_irq();
 *       NVIC_SystemReset();
 *       break;
 *
 * The matching packet command is:
 *
 *   $RESET,<seq>*<xor_checksum>
 */
